"""Cliente REGISTER: registro, refresco, baja y reintentos ante fallos."""

from __future__ import annotations

import random
import threading
import time
from enum import Enum
from typing import Callable

from .config import AccountConfig
from .digest import DigestAuthenticator
from .messages import (
    SipMessage,
    SipParseError,
    contact_expires,
    parse_messages_waiting,
    parse_message,
    random_token,
)
from .transport import UdpTransport, new_transport

USER_AGENT = "SIPip/0.2.0"
REGISTER_TIMEOUT = 5.0
POLL_INTERVAL = 0.25
REFRESH_RATIO = 0.85
REFRESH_JITTER = 0.05
RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
PERMANENT_RETRY_DELAY = 60.0
MAX_CHALLENGES = 3
MIN_EXPIRES = 60
PERMANENT_STATUS = frozenset({400, 403, 404, 405, 406, 415, 420, 480})

_INTERRUPTED = object()

STATUS_TEXT: dict[int, str] = {
    400: "Bad Request: el servidor rechazo la peticion (revisa dominio y sintaxis)",
    403: "Forbidden: usuario o contrasena incorrectos, o extension sin permiso",
    404: "Not Found: el dominio o la extension no existen en Issabel",
    405: "Method Not Allowed: REGISTER no permitido en esa URL",
    406: "Not Acceptable: cabeceras no aceptadas por el servidor",
    408: "Request Timeout: el servidor no responde a tiempo",
    423: "Interval Too Brief: intervalo de registro demasiado corto",
    480: "Temporarily Unavailable: extension ocupada",
    503: "Service Unavailable: servidor saturado o en mantenimiento",
}

EventCallback = Callable[["RegistrarState", str], None]


class RegistrarState(Enum):
    IDLE = "idle"
    REGISTERING = "registering"
    CHALLENGED = "challenged"
    REGISTERED = "registered"
    RETRYING = "retrying"
    FAILED = "failed"
    STOPPED = "stopped"


def build_register(
    account: AccountConfig,
    local_ip: str,
    local_port: int,
    call_id: str,
    cseq: int,
    expires: int,
    auth_header: str | None = None,
) -> tuple[bytes, str]:
    """Construye un REGISTER. Devuelve (datagrama, request-uri)."""
    transport = account.transport.lower()
    domain = account.domain or account.server_host
    user = account.extension
    request_uri = f"sip:{domain}"

    display = account.display_name or user
    tag = random_token(10)
    branch = f"z9hG4bK{random_token(20)}"

    headers = [
        ("Via", f"SIP/2.0/{transport.upper()} {local_ip}:{local_port};branch={branch};rport"),
        ("Max-Forwards", "70"),
        ("From", f'"{display}" <sip:{user}@{domain}>;tag={tag}'),
        ("To", f"<sip:{user}@{domain}>"),
        ("Call-ID", f"{call_id}"),
        ("CSeq", f"{cseq} REGISTER"),
        ("Contact", f"<sip:{user}@{local_ip}:{local_port};transport={transport}>"),
        ("Expires", str(expires)),
        ("Supported", "path"),
        ("Allow-Events", "presence"),
        ("User-Agent", USER_AGENT),
    ]
    if auth_header:
        headers.append(("Authorization", auth_header))
    headers.append(("Content-Length", "0"))

    start_line = f"REGISTER {request_uri} SIP/2.0"
    lines = [start_line, *(f"{name}: {value}" for name, value in headers)]
    text = "\r\n".join(lines) + "\r\n\r\n"
    return text.encode("utf-8"), request_uri


def resolve_expires(response: SipMessage, requested: int) -> int:
    """Expires efectivo segun la respuesta del servidor."""
    header = response.header("expires")
    if header and header.strip().isdigit():
        value = int(header.strip())
        if value > 0:
            return value
    for contact in response.headers_all("contact"):
        value = contact_expires(contact)
        if value:
            return value
    return requested


def describe_response(response: SipMessage) -> str:
    status = response.status or 0
    if status in STATUS_TEXT:
        return STATUS_TEXT[status]
    reason = (response.reason or "").strip()
    return f"El servidor respondio {status} {reason}".strip()


class Registrar:
    """Mantiene la extension registrada mediante un hilo dedicado."""

    def __init__(
        self,
        account: AccountConfig,
        password: str,
        on_event: EventCallback | None = None,
        contact_port: int | None = None,
    ) -> None:
        self.account = account
        self._password = password
        self._on_event = on_event
        self._contact_port = contact_port

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._interrupt = threading.Event()
        self._unregister_requested = False
        self._lock = threading.Lock()

        self._state = RegistrarState.IDLE
        self._detail = "Sin iniciar"
        self._expires_at: float | None = None
        self._last_challenge: str | None = None
        self._message_waiting = False
        self._voicemail_count = 0

    @property
    def state(self) -> RegistrarState:
        with self._lock:
            return self._state

    @property
    def detail(self) -> str:
        with self._lock:
            return self._detail

    @property
    def expires_at(self) -> float | None:
        with self._lock:
            return self._expires_at

    @property
    def message_waiting(self) -> bool:
        with self._lock:
            return self._message_waiting

    @property
    def voicemail_count(self) -> int:
        with self._lock:
            return self._voicemail_count

    def unread_voicemail(self) -> int:
        """Mensajes de voz pendientes segun el indicador del servidor."""
        with self._lock:
            return self._voicemail_count if self._message_waiting else 0

    def expires_remaining(self) -> int:
        with self._lock:
            if self._expires_at is None:
                return 0
            return max(0, int(self._expires_at - time.time()))

    def is_registered(self) -> bool:
        return self.state is RegistrarState.REGISTERED

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        errors = self.account.validate()
        if errors:
            raise ValueError("; ".join(errors))

        self._stop_event.clear()
        self._interrupt.clear()
        self._unregister_requested = False
        self._thread = threading.Thread(target=self._run, name="sip-registrar", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Desregistra (best effort) y detiene el hilo.

        Primero se pide la baja para que el servidor elimine el contacto y solo
        si el hilo no termina en el plazo se le fuerza la parada.
        """
        if not self.running:
            self._emit(RegistrarState.IDLE, "Cliente detenido")
            return

        self._unregister_requested = True
        self._interrupt.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._stop_event.set()
                thread.join(timeout=1.0)
        self._thread = None

    def unregister(self) -> None:
        """Pide la baja inmediata de la extension."""
        self._unregister_requested = True
        self._interrupt.set()

    def _emit(self, state: RegistrarState, detail: str) -> None:
        with self._lock:
            self._state = state
            self._detail = detail
            if state is not RegistrarState.REGISTERED:
                self._expires_at = None
        if self._on_event is not None:
            self._on_event(state, detail)

    def _wait(self, seconds: float) -> bool:
        """Espera interrumpible. Devuelve False si debe detenerse el hilo."""
        self._interrupt.wait(timeout=max(0.0, seconds))
        self._interrupt.clear()
        return not self._stop_event.is_set()

    def _await_response(self, transport: UdpTransport) -> object:
        """Espera una respuesta final. Devuelve _INTERRUPTED si hay que reevaluar."""
        deadline = time.monotonic() + REGISTER_TIMEOUT
        while True:
            if self._stop_event.is_set() or self._interrupt.is_set():
                self._interrupt.clear()
                return _INTERRUPTED

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            data = transport.recv(time.monotonic() + min(remaining, POLL_INTERVAL))
            if data is None:
                continue
            try:
                message = parse_message(data)
            except SipParseError as exc:
                self._emit(RegistrarState.REGISTERING, f"Datagrama descartado: {exc}")
                continue
            if message.is_request:
                continue
            status = message.status or 0
            if 100 <= status < 200:
                continue
            return message

    def _refresh_delay(self, expires: int) -> float:
        base = max(MIN_EXPIRES, expires) * REFRESH_RATIO
        return base * (1.0 + random.uniform(-REFRESH_JITTER, REFRESH_JITTER))

    def _run(self) -> None:
        account = self.account
        try:
            transport = new_transport(account.transport, account.server_host, account.server_port)
        except OSError as exc:
            self._emit(RegistrarState.FAILED, f"No se pudo abrir el socket de senalizacion: {exc}")
            return

        try:
            local_ip, local_port = transport.local_address
            contact_port = self._contact_port or local_port
            call_id = f"{random_token(20)}@{local_ip}"
            auth = DigestAuthenticator(account.extension, self._password)
            requested_expires = max(MIN_EXPIRES, account.expires)
            cseq = 0
            challenges = 0
            failures = 0

            while not self._stop_event.is_set():
                going_down = self._unregister_requested
                cseq += 1
                expires = 0 if going_down else requested_expires

                auth_header = None
                if auth.challenged:
                    _, uri = build_register(
                        account, local_ip, contact_port, call_id, cseq, expires
                    )
                    try:
                        auth_header = auth.build_header("REGISTER", uri)
                    except (ValueError, RuntimeError) as exc:
                        self._emit(RegistrarState.FAILED, f"Fallo calculando digest: {exc}")
                        return

                payload, _ = build_register(
                    account, local_ip, contact_port, call_id, cseq, expires, auth_header
                )

                label = "Desregistrando" if going_down else f"Enviando REGISTER (expires={expires})"
                self._emit(RegistrarState.REGISTERING, f"{label}, CSeq {cseq}")
                try:
                    transport.send(payload)
                except OSError as exc:
                    # En TCP la conexion puede estar cerrada por inactividad; se
                    # reconecta una vez y se reintenta antes de declarar el fallo.
                    try:
                        transport = new_transport(
                            account.transport, account.server_host, account.server_port
                        )
                        local_ip, _local_port = transport.local_address
                        transport.send(payload)
                    except OSError:
                        self._emit(RegistrarState.FAILED, f"Error enviando REGISTER: {exc}")
                        return

                response = self._await_response(transport)
                if response is _INTERRUPTED:
                    if self._stop_event.is_set():
                        break
                    continue

                status = response.status if response is not None else None

                if status in (401, 407):
                    if response is None or not self._handle_challenge(response, auth, challenges):
                        return
                    challenges += 1
                    failures = 0
                    continue

                if going_down:
                    reason = (
                        "sin respuesta del servidor"
                        if response is None
                        else describe_response(response)
                    )
                    self._emit(
                        RegistrarState.IDLE,
                        f"Baja no confirmada ({reason}). El contacto caducara solo.",
                    )
                    return

                if response is None:
                    detail = "Sin respuesta del servidor (timeout de senalizacion). Revisa IP, puerto y firewall."
                    failures += 1
                    self._emit(RegistrarState.RETRYING, detail)
                    if not self._wait(RETRY_DELAYS[min(failures - 1, len(RETRY_DELAYS) - 1)]):
                        break
                    continue

                if status is not None and 200 <= status < 300:
                    challenges = 0
                    failures = 0
                    effective = resolve_expires(response, requested_expires)
                    with self._lock:
                        self._expires_at = time.time() + effective
                        mwi = parse_messages_waiting(response.header("messages-waiting"))
                        if mwi is not None:
                            self._message_waiting, self._voicemail_count = mwi
                    if going_down:
                        self._emit(RegistrarState.IDLE, "Desregistrado correctamente")
                        return
                    extra = ""
                    unread = self.unread_voicemail()
                    if unread:
                        extra = f" | buzon de voz: {unread} mensaje(s)"
                    self._emit(
                        RegistrarState.REGISTERED,
                        f"Extension {account.extension} registrada en {account.server_host} "
                        f"({effective}s, IP {local_ip}){extra}",
                    )
                    if not self._wait(self._refresh_delay(effective)):
                        break
                    continue

                if status == 423:
                    header = response.header("min-expires")
                    minimum = int(header) if header and header.isdigit() else MIN_EXPIRES
                    requested_expires = max(MIN_EXPIRES, minimum + 1)
                    self._emit(
                        RegistrarState.RETRYING,
                        f"423 Interval Too Brief: expires ajustado a {requested_expires}s",
                    )
                    if not self._wait(1.0):
                        break
                    continue

                detail = describe_response(response)
                failures += 1
                if status in PERMANENT_STATUS:
                    self._emit(RegistrarState.FAILED, f"{detail}. Reintentando en 60s.")
                    if not self._wait(PERMANENT_RETRY_DELAY):
                        break
                    continue

                self._emit(RegistrarState.RETRYING, f"{detail}. Reintentando.")
                if not self._wait(RETRY_DELAYS[min(failures - 1, len(RETRY_DELAYS) - 1)]):
                    break
        finally:
            transport.close()
            with self._lock:
                current = self._state
            if current not in (RegistrarState.IDLE, RegistrarState.FAILED):
                self._emit(RegistrarState.STOPPED, "Sesion SIP finalizada")

    def _handle_challenge(
        self,
        response: SipMessage | None,
        auth: DigestAuthenticator,
        attempts: int,
    ) -> bool:
        """Procesa un 401/407. Devuelve False si el hilo debe terminar."""
        if response is None:
            return False
        header = response.header("www-authenticate") or response.header(
            "proxy-authenticate"
        )
        if not header:
            self._emit(
                RegistrarState.FAILED,
                "El servidor pidio autenticacion pero no envio WWW-Authenticate.",
            )
            return False
        if attempts >= MAX_CHALLENGES:
            self._emit(
                RegistrarState.FAILED,
                "Credenciales rechazadas repetidamente. Verifica extension y contrasena.",
            )
            return False
        try:
            auth.challenge_with(header)
        except ValueError as exc:
            self._emit(RegistrarState.FAILED, f"Autenticacion invalida: {exc}")
            return False
        realm = auth.challenge.get("realm", "?") if auth.challenge else "?"
        self._emit(RegistrarState.CHALLENGED, f"Autenticacion digest, realm '{realm}'")
        return True
