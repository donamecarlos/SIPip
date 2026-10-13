"""Motor de llamadas SIP real: INVITE, digest, RTP y transporte de voz.

Flujo soportado (salientes): INVITE -> 401/407 + Authorization -> 100/180
-> 200 OK (SDP) -> ACK -> RTP bidireccional con G.711 -> BYE. Tambien se
maneja CANCEL mientras suena y retransmision UDP con timers T1/T2.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace
from typing import Callable

from .audio import AudioEngine, DummyAudio
from .call import CallEngine, CallSession, CallState, normalize_target
from .config import AccountConfig
from .digest import DigestAuthenticator
from .g711 import DECODERS, ENCODERS
from .messages import (
    SipMessage,
    SipParseError,
    parse_message,
    random_token,
)
from .rtp import RtpSocket
from .sdp import SdpError, SdpMedia, SUPPORTED_CODECS, build_offer, parse_answer
from .transport import (
    UdpListener,
    UdpTransport,
    new_listener,
    new_transport,
)

USER_AGENT = "SIPip/0.2.0"
IDLE_POLL = 0.25
MEDIA_SAMPLE_RATE = 8000
MEDIA_FRAME_SAMPLES = 160
_SILENCE_PCM = b"\x00\x00" * MEDIA_FRAME_SAMPLES
INBOUND_RING_TIMEOUT = 45.0
INBOUND_ACK_TIMEOUT = 6.0

_STATUS_TEXT: dict[int, str] = {
    400: "Bad Request: el servidor rechazo la invitacion",
    403: "Forbidden: sin permiso para llamar a ese destino",
    404: "Not Found: numero o extension inexistente",
    408: "Request Timeout: el servidor no respondio a tiempo",
    480: "Temporarily Unavailable: destino ocupado o no disponible",
    484: "Address Incomplete: numero incompleto",
    486: "Busy Here: el destino esta ocupado",
    487: "Request Terminated: llamada cancelada",
    600: "Busy Everywhere: el destino esta ocupado",
    603: "Decline: el destino rechazo la llamada",
}


def status_text(status: int) -> str:
    return _STATUS_TEXT.get(status, f"El servidor respondio {status}")


def _pick_payload_type(codec: str) -> int:
    for name, payload_type, _rate in SUPPORTED_CODECS:
        if name == codec:
            return payload_type
    return 0


def _parse_from(from_value: str) -> tuple[str, str]:
    """Devuelve (nombre_mostrable, usuario) del header From de un INVITE."""
    display = ""
    if '"' in from_value:
        display = from_value.split('"')[1]
    uri = ""
    if "<" in from_value:
        uri = from_value.split("<")[1].split(">")[0]
    user = uri
    if "sip:" in uri.lower():
        user = uri.split(":", 1)[1].split("@")[0]
    return display.strip(), user.strip()


def _resolve_advertised_ip(server_host: str, server_port: int) -> str:
    """Devuelve la IP local por la que se llegaria al servidor SIP.

    Un socket conectado no envia trafico, solo fija la ruta y revela la
    interfaz de salida, correcta para anunciar en SDP y en el Contact.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((server_host, server_port))
        address, _ = probe.getsockname()[:2]
        return str(address)
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


_StopSentinel = object()


class SipCallEngine(CallEngine):
    """Motor funcional con hilo propio de senalizacion y media."""

    def __init__(
        self,
        on_event: Callable[[CallSession], None] | None = None,
        transport_cls: type[UdpTransport] = UdpTransport,
        rtp_cls: type[RtpSocket] = RtpSocket,
        audio_factory: Callable[[], AudioEngine | DummyAudio] | None = None,
        t1: float = 0.5,
        max_sends: int = 7,
        invite_timeout: float = 35.0,
    ) -> None:
        super().__init__(on_event)
        self._transport_cls = transport_cls
        self._rtp_cls = rtp_cls
        self._audio_factory = audio_factory or (lambda: AudioEngine())
        self._t1 = t1
        self._max_sends = max_sends
        self._invite_timeout = invite_timeout

        self.account: AccountConfig | None = None
        self._password = ""
        self._transport: UdpTransport | None = None
        self._rtp: RtpSocket | None = None
        self._audio: AudioEngine | DummyAudio | None = None

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self._media_stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._media_threads: list[threading.Thread] = []

        self._local_ip = "0.0.0.0"
        self._local_sip_port = 0
        self._call_id = ""
        self._tag = ""
        self._branch = ""
        self._invite_branch = ""
        self._cseq = 0
        self._auth: DigestAuthenticator | None = None
        self._target_uri = ""
        self._negotiated: SdpMedia | None = None
        self._send_pt = 0
        self._muting = False
        self._hold = False
        self._ack_map: dict[int, str] = {}  # cseq -> branch del re-INVITE
        self._last_remote_from = ""

        self._listener: UdpListener | None = None
        self._listener_port = 0
        self._listener_thread: threading.Thread | None = None
        self._listener_stop = threading.Event()
        self._inbound_req: SipMessage | None = None
        self._inbound_addr: tuple[str, int] | None = None
        self._inbound_call_id = ""
        self._inbound_tag = ""
        self._invite_body = b""
        self._inbound_target = ""
        self._inbound_peer = ""
        self._inbound_answered_body = ""
        self._inbound_answer = threading.Event()
        self._inbound_reject = threading.Event()
        self._inbound_ack = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_call(self, target: str, account: AccountConfig, password: str) -> CallSession:
        normalized = normalize_target(target, account.outbound_prefix)
        if normalized.error:
            self._set_session(
                CallSession(state=CallState.FAILED, detail=normalized.error, target=target)
            )
            return self.session

        with self._lock:
            if self.session.active:
                self._set_session(
                    CallSession(state=CallState.FAILED, detail="Ya hay una llamada en curso.")
                )
                return self.session
            self._cseq = 1

        self.account = account
        self._password = password
        self._call_id = f"{random_token(20)}@sipip"
        self._tag = random_token(10)
        self._stop.clear()
        self._interrupt.clear()
        self._media_stop.clear()
        self._thread = threading.Thread(target=self._run, name="sip-call", daemon=True)
        self._set_session(
            CallSession(
                target=normalized.number,
                peer=normalized.display,
                state=CallState.DIALING,
                detail="Llamando...",
                started_at=time.time(),
            )
        )
        self._thread.start()
        return self.session

    def hangup(self) -> None:
        active = self.session.active
        self._interrupt.set()
        self._media_stop.set()
        if active and self.session.state is CallState.INCOMING:
            self._inbound_reject.set()
        if not active:
            self._set_session(CallSession(state=CallState.IDLE))

    def answer(self) -> None:
        with self._lock:
            if self.session.state is not CallState.INCOMING:
                return
            self._inbound_answer.set()

    def reject_incoming(self) -> None:
        with self._lock:
            if self.session.state is not CallState.INCOMING:
                return
            self._inbound_reject.set()

    @property
    def listening_port(self) -> int:
        return self._listener_port

    def start_listening(self, port: int = 0) -> int:
        """Abre el punto de escucha de llamadas entrantes (idempotente)."""
        if self._listener is not None and not self._listener_stop.is_set():
            return self._listener_port
        account = self.account
        if account is not None:
            self._local_ip = _resolve_advertised_ip(account.server_host, account.server_port)
        transport_name = account.transport.lower() if account is not None else "udp"
        listener = new_listener(transport_name, local_port=port)
        self._listener = listener
        self._listener_port = listener.local_port
        self._listener_stop.clear()
        self._listener_thread = threading.Thread(
            target=self._listen_loop, name="sip-listen", daemon=True
        )
        self._listener_thread.start()
        return self._listener_port

    def send_dtmf(self, digits: str) -> None:
        if self._rtp is None or self.session.state is not CallState.CONNECTED:
            return
        for digit in digits:
            self._rtp.send_dtmf_event(digit, payload_type=101)

    def set_mute(self, enabled: bool) -> None:
        with self._lock:
            self._muting = enabled
            self.session = replace(self.session, muting=enabled)
            self._publish()

    def set_hold(self, enabled: bool) -> None:
        with self._lock:
            self._hold = enabled
            self.session = replace(self.session, on_hold=enabled)
            self._publish()
        if self.session.state in (CallState.CONNECTED, CallState.HOLDING):
            self._update_media_state()

    def close(self) -> None:
        self._listener_stop.set()
        self._inbound_answer.set()
        self._inbound_reject.set()
        self._stop.set()
        self._interrupt.set()
        self._media_stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
            self._thread = None
        listener_thread = self._listener_thread
        if listener_thread is not None:
            listener_thread.join(timeout=3.0)
            self._listener_thread = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._rtp is not None:
            self._rtp.close()
            self._rtp = None
        if self._audio is not None:
            self._audio.close()
            self._audio = None

    # ------------------------------------------------------------------
    # Verificacion de estado interna
    # ------------------------------------------------------------------

    def _set_session(self, session: CallSession) -> None:
        with self._lock:
            self.session = session
            self._publish()

    def _emit(self, state: CallState, detail: str, **changes: object) -> None:
        with self._lock:
            self.session = replace(self.session, state=state, detail=detail, **changes)
            self._publish()

    def _peer_tag(self) -> str:
        for part in self._last_remote_from.split(";")[1:]:
            if part.strip().startswith("tag="):
                return part.strip()[4:]
        return "sipip"

    def _uri(self) -> str:
        account = self.account
        assert account is not None
        domain = account.domain or account.server_host
        return f"sip:{self.session.target}@{domain}"

    def _sip_transport(self) -> str:
        account = self.account
        return (account.transport.upper() if account is not None else "UDP")

    def _send_sip(self, data: bytes) -> None:
        if self._transport is None:
            raise OSError("Transporte SIP cerrado.")
        self._transport.send(data)

    def _recv(self, deadline: float) -> SipMessage | None | object:
        transport = self._transport
        if transport is None or self._stop.is_set():
            return _StopSentinel
        data = transport.recv(deadline)
        if data is None:
            return None
        try:
            return parse_message(data)
        except SipParseError:
            return None

    # ------------------------------------------------------------------
    # Construccion de mensajes
    # ------------------------------------------------------------------

    def _base_pending(self, method: str = "INVITE") -> list[tuple[str, str]]:
        account = self.account
        assert account is not None
        transport_name = account.transport.lower()
        domain = account.domain or account.server_host
        user = account.extension
        display = account.display_name or user
        headers = [
            ("Via", f"SIP/2.0/{transport_name.upper()} {self._local_ip}:{self._local_sip_port};"
                    f"branch={self._branch};rport"),
            ("Max-Forwards", "70"),
            ("From", f'"{display}" <sip:{user}@{domain}>;tag={self._tag}'),
            ("To", f"<sip:{self.session.target}@{domain}>"),
            ("Call-ID", f"{self._call_id}"),
            ("CSeq", f"{self._cseq} {method}"),
            ("Contact", f"<sip:{user}@{self._local_ip}:{self._local_sip_port};"
                        f"transport={transport_name}>"),
            ("Content-Type", "application/sdp"),
            ("User-Agent", USER_AGENT),
            ("Allow", "INVITE, ACK, CANCEL, BYE, OPTIONS, NOTIFY, MESSAGE, INFO"),
        ]
        return headers

    def _authorization(self, method: str) -> str | None:
        auth = self._auth
        if auth is None or not auth.challenged:
            return None
        try:
            return auth.build_header(method, self._uri())
        except (ValueError, RuntimeError):
            return None

    def _with_body(self, headers: list[tuple[str, str]], body: str) -> bytes:
        payload = body.encode("utf-8")
        headers.append(("Content-Length", str(len(payload))))
        lines = [f"INVITE {self._uri()} SIP/2.0"]
        lines.extend(f"{name}: {value}" for name, value in headers)
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload

    def _build_invite(self, body: str, method: str = "INVITE") -> bytes:
        headers = self._base_pending(method)
        auth = self._authorization(method)
        if auth:
            headers.append(("Authorization", auth))
        return self._with_body(headers, body)

    def _build_bye(self, cseq: int) -> bytes:
        account = self.account
        assert account is not None
        domain = account.domain or account.server_host
        user = account.extension
        branch = f"z9hG4bK{random_token(20)}"
        peer = self.session.peer or self.session.target
        lines = [
            f"BYE {self._uri()} SIP/2.0",
            f"Via: SIP/2.0/{self._sip_transport()} {self._local_ip}:{self._local_sip_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{user}@{domain}>;tag={self._tag}",
            f"To: <sip:{peer}@{domain}>;tag={self._peer_tag()}",
            f"Call-ID: {self._call_id}",
            f"CSeq: {cseq} BYE",
            f"User-Agent: {USER_AGENT}",
            "Content-Length: 0",
        ]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")

    def _build_cancel(self, cseq: int) -> bytes:
        account = self.account
        assert account is not None
        domain = account.domain or account.server_host
        user = account.extension
        branch = f"z9hG4bK{random_token(20)}"
        lines = [
            f"CANCEL {self._uri()} SIP/2.0",
            f"Via: SIP/2.0/{self._sip_transport()} {self._local_ip}:{self._local_sip_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{user}@{domain}>;tag={self._tag}",
            f"To: <sip:{self.session.target}@{domain}>",
            f"Call-ID: {self._call_id}",
            f"CSeq: {cseq} CANCEL",
            f"User-Agent: {USER_AGENT}",
            "Content-Length: 0",
        ]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")

    def _build_ack(self, cseq: int, branch: str, peer_tag: str = "") -> bytes:
        account = self.account
        assert account is not None
        domain = account.domain or account.server_host
        user = account.extension
        to_tag = f";tag={peer_tag}" if peer_tag else ""
        lines = [
            f"ACK {self._uri()} SIP/2.0",
            f"Via: SIP/2.0/{self._sip_transport()} {self._local_ip}:{self._local_sip_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{user}@{domain}>;tag={self._tag}",
            f"To: <sip:{self.session.target}@{domain}>{to_tag}",
            f"Call-ID: {self._call_id}",
            f"CSeq: {cseq} ACK",
            "Content-Length: 0",
        ]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")

    # ------------------------------------------------------------------
    # Worker: fase INVITE
    # ------------------------------------------------------------------

    def _run(self) -> None:
        self._transport = None
        self._rtp = None
        self._audio = None
        try:
            account = self.account
            assert account is not None
            transport = new_transport(account.transport, account.server_host, account.server_port)
            self._transport = transport
            self._local_ip, self._local_sip_port = transport.local_address

            rtp = self._rtp_cls(self._local_ip, 0)
            self._rtp = rtp
            self._auth = DigestAuthenticator(account.extension, self._password)

            answer = self._invite_phase(transport, rtp)
            if answer is None:
                return
            self._answered_phase(transport, rtp, answer)
        except OSError as exc:
            self._emit(CallState.FAILED, f"Error de red: {exc}")
        except Exception as exc:  # noqa: BLE001 - la app nunca debe caer por la llamada
            self._emit(CallState.FAILED, f"Error inesperado en la llamada: {exc}")
        finally:
            self._teardown()

    def _invite_phase(self, transport: UdpTransport, rtp: RtpSocket) -> _AnswerSnapshot | None:
        offer = build_offer(self._local_ip, rtp.local_port)
        started = time.monotonic()
        challenge_attempts = 0
        provisional = False

        self._branch = f"z9hG4bK{random_token(20)}"
        self._invite_branch = self._branch
        timer = self._t1
        retransmits = 0
        next_retransmit = time.monotonic() + timer

        self._send_sip(self._build_invite(offer))
        retransmits += 1

        while True:
            if self._interrupt.is_set():
                self._cancel_call(self._cseq)
                return None

            message = self._recv(min(time.monotonic() + IDLE_POLL, next_retransmit))

            if message is _StopSentinel:
                return None

            now = time.monotonic()
            if now - started > self._invite_timeout:
                self._emit(CallState.FAILED, "Tiempo de llamada agotado. Intentelo de nuevo.")
                return None

            if message is None:
                if provisional:
                    next_retransmit = now + IDLE_POLL
                    continue
                if retransmits >= self._max_sends:
                    self._emit(
                        CallState.FAILED,
                        "Sin respuesta del servidor. Revisa servidor, puerto y firewall.",
                    )
                    return None
                if now >= next_retransmit:
                    self._send_sip(self._build_invite(offer))
                    retransmits += 1
                    next_retransmit = now + timer
                    timer = min(timer * 2, 4.0)
                continue

            if message.is_request:
                continue

            status = message.status or 0
            if 100 <= status < 200:
                provisional = True
                if status in (180, 183):
                    self._emit(CallState.RINGING, "Destino sonando...")
                continue

            if status in (401, 407):
                header = message.header("www-authenticate") or message.header(
                    "proxy-authenticate"
                )
                if self._auth is None or not header:
                    self._emit(CallState.FAILED, "El servidor pidio autenticacion sin header.")
                    return None
                try:
                    self._auth.challenge_with(header)
                except ValueError as exc:
                    self._emit(CallState.FAILED, f"Autenticacion invalida: {exc}")
                    return None
                challenge_attempts += 1
                if challenge_attempts >= 3:
                    self._emit(
                        CallState.FAILED,
                        "Credenciales rechazadas repetidamente. Verifica la contrasena.",
                    )
                    return None
                self._branch = f"z9hG4bK{random_token(20)}"
                self._invite_branch = self._branch
                self._send_sip(self._build_invite(offer))
                retransmits += 1
                continue

            if 200 <= status < 300:
                try:
                    media = parse_answer(message.body.decode("utf-8", errors="replace"))
                except (SdpError, ValueError) as exc:
                    self._emit(CallState.FAILED, f"Respuesta SDP invalida: {exc}")
                    return None
                self._emit(CallState.ANSWERED, "Contestada", answered_at=time.time())
                return _AnswerSnapshot(message, media)

            detail = status_text(status)
            if status == 487:
                self._send_sip(self._build_ack(self._cseq, self._invite_branch))
                self._emit(CallState.ENDED, "Llamada terminada")
            else:
                self._send_sip(self._build_ack(self._cseq, self._invite_branch))
                self._emit(CallState.FAILED, detail)
            return None

    def _cancel_call(self, cseq: int) -> None:
        try:
            self._send_sip(self._build_cancel(cseq))
        except OSError:
            pass
        self._emit(CallState.ENDED, "Llamada cancelada")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not self._stop.is_set():
            message = self._recv(deadline)
            if message is _StopSentinel:
                return
            if message is None:
                continue
            if message.is_request:
                continue
            status = message.status or 0
            if status == 200:
                continue
            if status == 487:
                try:
                    self._send_sip(self._build_ack(self._cseq, self._invite_branch))
                except OSError:
                    pass
                return

    # ------------------------------------------------------------------
    # Worker: fase de media
    # ------------------------------------------------------------------

    def _answered_phase(
        self, transport: UdpTransport, rtp: RtpSocket, answer: _AnswerSnapshot
    ) -> None:
        media = answer.media
        self._negotiated = media
        self._send_pt = _pick_payload_type(media.codec)
        self._last_remote_from = answer.response.header("from") or ""

        audio: AudioEngine | DummyAudio | None = None
        try:
            audio = self._audio_factory()
            audio.open()
        except Exception as exc:  # noqa: BLE001 - sin audio la llamada continua
            audio = None

        try:
            self._send_sip(self._build_ack(self._cseq, self._invite_branch))
        except OSError:
            pass

        if self._stop.is_set():
            return

        rtp.set_remote(media.connection_ip, media.port)
        self._audio = audio
        if audio is not None:
            try:
                audio.prefill()
            except OSError:
                pass
        self._emit(
            CallState.CONNECTED,
            "En conversacion",
            answered_at=self.session.answered_at,
        )
        self._media_stop.clear()
        running = self._spawn_media(rtp, media, audio)
        if not running:
            return

        while not self._stop.is_set():
            if self._interrupt.is_set():
                cseq = self._cseq + 1
                self._cseq = cseq
                try:
                    self._send_sip(self._build_bye(cseq))
                except OSError:
                    pass
                self._finish_call()
                return

            message = self._recv(time.monotonic() + IDLE_POLL)
            if message is _StopSentinel:
                return
            if message is None:
                continue
            if message.is_request:
                if message.method == "BYE":
                    self._reply_ok(message)
                    self._finish_call()
                    return
                if message.method == "INVITE":
                    self._reply_ok(message)
                    continue
                if message.method == "OPTIONS":
                    self._reply_ok(message)
                    continue
                continue
            status = message.status or 0
            cseq_header = (message.header("cseq") or "").split()
            request_cseq = int(cseq_header[0]) if cseq_header and cseq_header[0].isdigit() else -1
            if 200 <= status < 300 and request_cseq in self._ack_map:
                branch = self._ack_map.pop(request_cseq)
                try:
                    self._send_sip(self._build_ack(request_cseq, branch))
                except OSError:
                    pass
                continue

    def _reply_ok(self, request: SipMessage) -> None:
        account = self.account
        assert account is not None
        transport_name = account.transport.lower()
        lines = [
            "SIP/2.0 200 OK",
            f"Via: {request.header('via')}",
            f"From: {request.header('to')}",
            f"To: {request.header('from')}",
            f"Call-ID: {request.header('call-id')}",
            f"CSeq: {request.header('cseq')}",
            f"Contact: <sip:{account.extension}@{self._local_ip}:{self._listener_port or self._local_sip_port};"
            f"transport={transport_name}>",
            "Content-Length: 0",
        ]
        try:
            self._send_sip(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        except OSError:
            pass

    def _finish_call(self) -> None:
        self._stop_media()
        with self._lock:
            answered = self.session.answered_at
            duration = int((time.time() - answered)) if answered else 0
            self.session = replace(
                self.session,
                state=CallState.ENDED,
                detail="Llamada finalizada",
            )
            self.session.duration = duration
            self._publish()

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    def _spawn_media(self, rtp: RtpSocket, media: SdpMedia, audio) -> bool:
        decoder = DECODERS[media.codec]
        encoder = ENCODERS[media.codec]
        send_pt = self._send_pt
        silent = encoder(_SILENCE_PCM)

        def tx() -> None:
            next_slot = time.monotonic()
            marker = True
            while not self._media_stop.is_set() and not self._stop.is_set():
                if self._muting or audio is None:
                    frame = silent
                else:
                    frame = encoder(audio.read(MEDIA_FRAME_SAMPLES))
                try:
                    rtp.send_payload(frame, send_pt, marker=marker)
                except OSError:
                    return
                marker = False
                next_slot += 0.02
                time.sleep(max(0.0, next_slot - time.monotonic()))

        def rx() -> None:
            while not self._media_stop.is_set() and not self._stop.is_set():
                for frame in rtp.drain():
                    if frame.payload_type != send_pt:
                        continue
                    if audio is None:
                        continue
                    audio.play(decoder(frame.payload))

        threads = [
            threading.Thread(target=tx, name="sip-mic", daemon=True),
            threading.Thread(target=rx, name="sip-speaker", daemon=True),
        ]
        threads[0].start()
        threads[1].start()
        self._media_threads = threads
        return True

    def _stop_media(self) -> None:
        self._media_stop.set()
        threads, self._media_threads = self._media_threads, []
        for thread in threads:
            thread.join(timeout=1.0)
        if self._rtp is not None:
            self._rtp.close()
            self._rtp = None

    # ------------------------------------------------------------------
    # Hold / retransmisión
    # ------------------------------------------------------------------

    def _update_media_state(self) -> None:
        rtp = self._rtp
        if rtp is None:
            return
        self._cseq += 1
        cseq = self._cseq
        branch = f"z9hG4bK{random_token(20)}"
        self._branch = branch
        self._ack_map[cseq] = branch
        with self._lock:
            holding = self._hold
        if holding:
            codec_name = self._negotiated.codec if self._negotiated else "PCMU"
            rate = self._negotiated.clock_rate if self._negotiated else 8000
            body = (
                f"v=0\r\no=SIPip 0 0 IN IP4 {self._local_ip}\r\ns=hold\r\n"
                "c=IN IP4 0.0.0.0\r\nt=0 0\r\n"
                f"m=audio {rtp.local_port} RTP/AVP {self._send_pt}\r\n"
                f"a=rtpmap:{self._send_pt} {codec_name}/{rate}\r\na=sendonly\r\n"
            )
        else:
            body = build_offer(self._local_ip, rtp.local_port)
        headers = self._base_pending()
        auth = self._authorization("INVITE")
        if auth:
            headers.append(("Authorization", auth))
        try:
            self._send_sip(self._with_body(headers, body))
        except OSError:
            pass
        self._emit(
            CallState.HOLDING if holding else CallState.CONNECTED,
            "En espera" if holding else "En conversacion",
            on_hold=holding,
        )

    def _teardown(self) -> None:
        self._stop_media()
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        audio = self._audio
        self._audio = None
        if audio is not None:
            try:
                audio.close()
            except OSError:
                pass
        with self._lock:
            current = self.session.state
        if current in (
            CallState.DIALING,
            CallState.RINGING,
            CallState.ANSWERED,
            CallState.CONNECTED,
            CallState.HOLDING,
        ):
            self._set_session(
                CallSession(
                    state=CallState.ENDED,
                    detail="Llamada terminada",
                    target=self.session.target,
                    peer=self.session.peer,
                )
            )


# ------------------------------------------------------------------
    # Llamadas entrantes (INVITE recibido sobre el listener)
    # ------------------------------------------------------------------

    def _listen_loop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._listener_stop.is_set():
            result = listener.recvfrom(time.monotonic() + IDLE_POLL)
            if self._listener_stop.is_set():
                return
            if result is None:
                continue
            data, addr = result
            try:
                message = parse_message(data)
            except SipParseError:
                continue
            if not message.is_request:
                continue
            method = message.method
            call_id = message.header("call-id") or ""
            if method == "OPTIONS":
                self._send_listener(self._inbound_response(message, 200, "OK"), addr)
                continue
            if method == "INVITE":
                if self._inbound_call_id and call_id == self._inbound_call_id:
                    continue  # el dialogo en curso se gestiona en su propia fase
                self._offer_inbound(message, addr)
                continue
            if self._inbound_call_id and call_id == self._inbound_call_id:
                if method == "BYE":
                    self._send_listener(self._inbound_response(message, 200, "OK"), addr)
                    self._inbound_ended("Llamada finalizada")
                    continue
                if method == "ACK":
                    self._inbound_ack.set()
                    continue

    def _offer_inbound(self, request: SipMessage, addr: tuple[str, int]) -> None:
        with self._lock:
            if self.session.active or self._inbound_call_id:
                self._send_listener(self._inbound_response(request, 486, "Busy Here"), addr)
                return
            if self.account is None:
                self._send_listener(self._inbound_response(request, 480, "Temporarily Unavailable"), addr)
                return
        display, user = _parse_from(request.header("from") or "")
        self._inbound_req = request
        self._inbound_addr = addr
        self._inbound_call_id = request.header("call-id") or random_token(20)
        self._inbound_tag = random_token(10)
        self._invite_body = request.body
        self._inbound_target = user
        self._inbound_peer = display or user
        self._inbound_answered_body = ""
        self._inbound_answer.clear()
        self._inbound_reject.clear()
        self._inbound_ack.clear()
        self._interrupt.clear()
        self._media_stop.clear()
        self._send_listener(self._inbound_response(request, 100, "Trying"), addr)
        self._send_listener(self._inbound_response(request, 180, "Ringing"), addr)
        self._set_session(
            CallSession(
                target=user,
                peer=display or user,
                state=CallState.INCOMING,
                detail=f"Llamada entrante de {display or user}",
                started_at=time.time(),
            )
        )
        self._await_inbound_answer(addr)

    def _await_inbound_answer(self, addr: tuple[str, int]) -> None:
        ring_started = time.monotonic()
        while True:
            if self._listener_stop.is_set():
                return
            if self._interrupt.is_set() or self._inbound_reject.is_set():
                self._finish_inbound_rejected(addr, "Llamada rechazada")
                return
            if self._inbound_answer.is_set():
                self._begin_inbound_call(addr)
                return
            if time.monotonic() - ring_started > INBOUND_RING_TIMEOUT:
                self._finish_inbound_rejected(addr, "Llamada no contestada", 487)
                return

            result = self._listener.recvfrom(time.monotonic() + IDLE_POLL)
            if result is None:
                continue
            try:
                message = parse_message(result[0])
            except SipParseError:
                continue
            if not message.is_request:
                continue
            if (message.header("call-id") or "") != self._inbound_call_id:
                continue
            if message.method == "INVITE":
                self._send_listener(self._inbound_response(message, 180, "Ringing"), addr)
            elif message.method == "CANCEL":
                self._send_listener(self._inbound_response(message, 200, "OK"), addr)
                self._send_listener(
                    self._inbound_response(message, 487, "Request Terminated"), addr
                )
                self._inbound_ended("El llamante cancelo la llamada")
                return

    def _finish_inbound_rejected(
        self, addr: tuple[str, int], detail: str, status: int = 486
    ) -> None:
        request = self._inbound_req
        if request is not None:
            reason = {486: "Busy Here", 487: "Request Terminated"}.get(status, "Busy Here")
            self._send_listener(self._inbound_response(request, status, reason), addr)
        self._inbound_ended(detail)

    def _begin_inbound_call(self, addr: tuple[str, int]) -> None:
        request = self._inbound_req
        if request is None or self.account is None:
            self._inbound_ended("Llamada terminada")
            return
        local_ip = self._local_ip
        rtp = self._rtp_cls(local_ip, 0)
        self._rtp = rtp
        offer = build_offer(local_ip, rtp.local_port)
        self._inbound_answered_body = offer
        contact = (
            f"<sip:{self.account.extension}@{local_ip}:{self._listener_port};"
            f"transport={self.account.transport.lower()}>"
        )
        try:
            self._send_listener(
                self._inbound_response(
                    request, 200, "OK", body=offer, contact=contact, to_tag=self._inbound_tag
                ),
                addr,
            )
        except OSError:
            self._inbound_ended("Error de red al responder")
            return
        self._emit(CallState.ANSWERED, "Contestada", answered_at=time.time())
        if not self._wait_inbound_ack(addr):
            self._inbound_ended("No se recibio el ACK del llamante")
            return
        self._media_phase_inbound(addr)

    def _wait_inbound_ack(self, addr: tuple[str, int], timeout: float = INBOUND_ACK_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while not self._listener_stop.is_set():
            if self._interrupt.is_set() or self._inbound_reject.is_set():
                return False
            if time.monotonic() >= deadline:
                return False
            result = self._listener.recvfrom(time.monotonic() + 0.3)
            if result is None:
                continue
            try:
                message = parse_message(result[0])
            except SipParseError:
                continue
            if not message.is_request:
                continue
            if (message.header("call-id") or "") != self._inbound_call_id:
                continue
            if message.method == "ACK":
                return True
            if message.method == "BYE":
                self._send_listener(self._inbound_response(message, 200, "OK"), addr)
                return False
            if message.method == "INVITE":
                self._send_listener(
                    self._inbound_response(
                        message, 200, "OK", body=self._inbound_answered_body,
                        to_tag=self._inbound_tag,
                    ),
                    addr,
                )
        return False

    def _media_phase_inbound(self, addr: tuple[str, int]) -> None:
        body = self._invite_body.decode("utf-8", errors="replace")
        try:
            media = parse_answer(body)
        except SdpError as exc:
            self._inbound_ended(f"Oferta SDP invalida: {exc}")
            return
        rtp = self._rtp
        if rtp is None:
            self._inbound_ended("Sin puerto RTP")
            return
        self._negotiated = media
        self._send_pt = _pick_payload_type(media.codec)
        self._last_remote_from = (self._inbound_req.header("from") or "") if self._inbound_req else ""

        audio: AudioEngine | DummyAudio | None = None
        try:
            audio = self._audio_factory()
            audio.open()
        except Exception:  # noqa: BLE001 - sin audio la llamada continua
            audio = None
        self._audio = audio
        rtp.set_remote(media.connection_ip, media.port)
        if audio is not None:
            try:
                audio.prefill()
            except OSError:
                pass
        self._emit(CallState.CONNECTED, "En conversacion", answered_at=self.session.answered_at)

        self._media_stop.clear()
        if not self._spawn_media(rtp, media, audio):
            self._inbound_ended("Llamada finalizada")
            return

        while not self._stop.is_set():
            if self._interrupt.is_set():
                self._send_inbound_bye(addr)
                self._inbound_ended("Llamada finalizada")
                return
            result = self._listener.recvfrom(time.monotonic() + IDLE_POLL)
            if result is None:
                continue
            try:
                message = parse_message(result[0])
            except SipParseError:
                continue
            if not message.is_request:
                continue
            if (message.header("call-id") or "") != self._inbound_call_id:
                continue
            if message.method == "BYE":
                self._send_listener(self._inbound_response(message, 200, "OK"), addr)
                self._inbound_ended("Llamada finalizada")
                return
            if message.method == "INVITE":
                self._send_listener(
                    self._inbound_response(
                        message, 200, "OK", body=self._inbound_answered_body,
                        to_tag=self._inbound_tag,
                    ),
                    addr,
                )
                continue

    def _send_inbound_bye(self, addr: tuple[str, int]) -> None:
        request = self._inbound_req
        if request is None or self.account is None:
            return
        from_header = request.header("from") or ""
        uri = from_header.split("<")[-1].split(">")[0] if "<" in from_header else ""
        if not uri:
            return
        self._cseq += 1
        branch = f"z9hG4bK{random_token(20)}"
        lines = [
            f"BYE {uri} SIP/2.0",
            f"Via: SIP/2.0/{self._sip_transport()} {self._local_ip}:{self._listener_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: {request.header('to')};tag={self._inbound_tag}",
            f"To: {from_header}",
            f"Call-ID: {self._inbound_call_id}",
            f"CSeq: {self._cseq} BYE",
            f"User-Agent: {USER_AGENT}",
            "Content-Length: 0",
        ]
        self._send_listener(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"), addr)

    def _inbound_ended(self, detail: str) -> None:
        self._stop_media()
        audio = self._audio
        self._audio = None
        if audio is not None:
            try:
                audio.close()
            except OSError:
                pass
        answered = self.session.answered_at
        duration = int(time.time() - answered) if answered else 0
        self._clear_inbound()
        self._set_session(
            CallSession(
                state=CallState.ENDED,
                detail=detail,
                target=self.session.target,
                peer=self.session.peer,
                duration=duration,
            )
        )

    def _clear_inbound(self) -> None:
        self._inbound_req = None
        self._inbound_addr = None
        self._inbound_call_id = ""
        self._inbound_tag = ""
        self._invite_body = b""
        self._inbound_target = ""
        self._inbound_peer = ""
        self._inbound_answered_body = ""

    def _send_listener(self, data: bytes, addr: tuple[str, int]) -> None:
        listener = self._listener
        if listener is None:
            raise OSError("Listener SIP cerrado.")
        listener.sendto(data, addr)

    def _inbound_response(
        self,
        request: SipMessage,
        status: int,
        reason: str,
        body: bytes | str = b"",
        contact: str = "",
        to_tag: str = "",
    ) -> bytes:
        if isinstance(body, str):
            body = body.encode("utf-8")
        to_header = request.header("to") or ""
        from_header = request.header("from") or ""
        local_tag = f";tag={to_tag}" if to_tag else ""
        lines = [f"SIP/2.0 {status} {reason}".rstrip()]
        lines.append(f"Via: {request.header('via')}")
        lines.append(f"To: {from_header}{local_tag}")
        lines.append(f"From: {to_header}")
        lines.append(f"Call-ID: {request.header('call-id')}")
        lines.append(f"CSeq: {request.header('cseq')}")
        if contact:
            lines.append(f"Contact: {contact}")
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


class _AnswerSnapshot:
    __slots__ = ("response", "media")

    def __init__(self, response: SipMessage, media: SdpMedia) -> None:
        self.response = response
        self.media = media