"""Servicio SIP para la interfaz Kivy: cablea config, registro, llamadas y datos."""

from __future__ import annotations

from collections.abc import Callable

from kivy.clock import Clock

from sipclient import storage
from sipclient.call import CallSession, CallState
from sipclient.config import AccountConfig, load_config, save_config
from sipclient.engine import SipCallEngine
from sipclient.registrar import Registrar, RegistrarState


def is_android() -> bool:
    try:
        import android  # type: ignore[attr-defined]  # noqa: F401

        return True
    except Exception:
        return False


def default_audio_factory():
    if is_android():
        try:
            from sipclient.android_audio import AndroidAudioEngine

            return lambda: AndroidAudioEngine()
        except Exception:  # noqa: BLE001
            pass
    try:
        from sipclient.audio import AudioEngine

        return lambda: AudioEngine()
    except Exception:  # noqa: BLE001
        from sipclient.audio import DummyAudio

        return lambda: DummyAudio()


RegistrarCallback = Callable[[RegistrarState, str], None]
CallEventCallback = Callable[[CallSession], None]


class SipService:
    """Mantiene la configuracion, el registro y el motor de llamadas vivos."""

    def __init__(self) -> None:
        self.config = load_config()
        self.password = self._load_password(self.config)
        self.on_registrar: RegistrarCallback | None = None
        self.on_call: CallEventCallback | None = None
        self._registrar: Registrar | None = None
        self._engine: SipCallEngine | None = None
        self._tones = None
        self._store = None

    @property
    def store(self):
        if self._store is None:
            from sipclient.datastore import Database

            self._store = Database()
        return self._store

    def _load_password(self, config: AccountConfig) -> str:
        if config.extension and config.keep_password:
            try:
                return storage.load_password(config.server_host, config.extension) or ""
            except storage.CredentialError:
                return ""
        return ""

    @property
    def tones(self):
        if self._tones is None:
            if is_android():
                from sipclient.android_tonos import AndroidTones

                self._tones = AndroidTones()
            else:
                from sipclient.tonos import TonosPlayer

                self._tones = TonosPlayer()
        return self._tones

    # ------------------------------------------------------------------
    # Configuracion
    # ------------------------------------------------------------------

    def apply_account(
        self,
        server_host: str,
        server_port: int,
        extension: str,
        password: str,
        domain: str = "",
        display_name: str = "",
        transport: str = "udp",
        outbound_prefix: str = "",
        keep_password: bool = True,
    ) -> list[str]:
        account = AccountConfig(
            server_host=server_host.strip().lower(),
            server_port=int(server_port),
            extension=extension.strip(),
            domain=domain.strip() or "",
            display_name=display_name.strip() or "",
            transport=transport,
            outbound_prefix=outbound_prefix.strip(),
            keep_password=keep_password,
        )
        errors = account.validate()
        if errors:
            return errors
        previous = self.config
        self.config = account
        save_config(account)
        if keep_password and account.extension:
            try:
                storage.store_password(account.server_host, account.extension, password)
            except storage.CredentialError:
                pass
        self.password = password
        if self.is_registered and (
            previous.server_host,
            previous.server_port,
            previous.extension,
            previous.transport,
        ) != (
            account.server_host,
            account.server_port,
            account.extension,
            account.transport,
        ):
            self.stop()
        return []

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    @property
    def registrar(self) -> Registrar | None:
        return self._registrar

    @property
    def is_registered(self) -> bool:
        return bool(self._registrar and self._registrar.is_registered())

    @property
    def registration_detail(self) -> str:
        if self._registrar is None:
            return "Sin iniciar"
        return self._registrar.detail

    def start_registration(self) -> None:
        self.stop()
        self._engine = SipCallEngine(
            on_event=self._thread_safe_call_event,
            audio_factory=default_audio_factory(),
        )
        self._engine.start_listening(self.config.sip_local_port)
        self._registrar = Registrar(
            self.config,
            self.password,
            on_event=self._thread_safe_registrar_event,
            contact_port=self._engine.listening_port,
        )
        try:
            self._registrar.start()
        except ValueError as exc:
            self._notify_registrar(RegistrarState.FAILED, f"{exc}")

    def stop(self) -> None:
        if self._registrar is not None:
            try:
                self._registrar.stop(timeout=1.5)
            except Exception:  # noqa: BLE001
                pass
            self._registrar = None
        self._notify_registrar(RegistrarState.IDLE, "Detenido")

    def _thread_safe_registrar_event(self, state: RegistrarState, detail: str) -> None:
        Clock.schedule_once(lambda _dt: self._notify_registrar(state, detail), 0)

    def _notify_registrar(self, state: RegistrarState, detail: str) -> None:
        if self.on_registrar is not None:
            self.on_registrar(state, detail)

    # ------------------------------------------------------------------
    # Llamadas
    # ------------------------------------------------------------------

    @property
    def engine(self) -> SipCallEngine:
        if self._engine is None:
            self._engine = SipCallEngine(
                on_event=self._thread_safe_call_event,
                audio_factory=default_audio_factory(),
            )
        return self._engine

    def _thread_safe_call_event(self, session: CallSession) -> None:
        Clock.schedule_once(lambda _dt: self._dispatch_call_event(session), 0)

    def _dispatch_call_event(self, session: CallSession) -> None:
        if self.on_call is not None:
            self.on_call(session)

    def start_call(self, number: str) -> None:
        session = self.engine.start_call(number, self.config, self.password)

    def answer(self) -> None:
        self.engine.answer()

    def reject(self) -> None:
        self.engine.reject_incoming()
        self.tones.stop()

    def hangup(self) -> None:
        self.tones.stop()
        self.engine.hangup()

    def send_dtmf(self, digits: str) -> None:
        if self.engine.session.active:
            self.engine.send_dtmf(digits)

    def toggle_mute(self) -> bool:
        enabled = not self.engine.session.muting
        self.engine.set_mute(enabled)
        return enabled

    def toggle_hold(self) -> bool:
        enabled = not self.engine.session.on_hold
        self.engine.set_hold(enabled)
        return enabled

    def shutdown(self) -> None:
        self.stop()
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
        if self._store is not None:
            try:
                self._store.close()
            except Exception:  # noqa: BLE001
                pass
            self._store = None
        try:
            self.tones.close()
        except Exception:  # noqa: BLE001
            pass