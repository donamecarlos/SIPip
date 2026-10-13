"""Contrato del motor de llamadas.

La senalizacion de llamada (INVITE/SDP/RTP) y el audio se implementan en la
fase siguiente. Aqui queda la interfaz que consumira el marcador, de modo que
cambiar el motor no obligue a tocar la interfaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .config import AccountConfig

ALLOWED_TARGET_CHARS = re.compile(r"^[0-9+*#]+$")
MAX_TARGET_LENGTH = 64
EXTENSION_LENGTH = 6


class CallState(Enum):
    IDLE = "idle"
    INCOMING = "incoming"
    DIALING = "dialing"
    RINGING = "ringing"
    ANSWERED = "answered"
    CONNECTED = "connected"
    HOLDING = "holding"
    ENDED = "ended"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


STATE_LABELS = {
    CallState.IDLE: "Libre",
    CallState.INCOMING: "Llamada entrante",
    CallState.DIALING: "Marcando",
    CallState.RINGING: "Recibiendo tono de llamada",
    CallState.ANSWERED: "Contestada",
    CallState.CONNECTED: "En conversacion",
    CallState.HOLDING: "En espera",
    CallState.ENDED: "Llamada finalizada",
    CallState.FAILED: "Llamada fallida",
    CallState.UNSUPPORTED: "No disponible",
}


@dataclass
class NormalizedTarget:
    number: str
    display: str
    is_extension: bool
    error: str | None = None


def normalize_target(raw: str, outbound_prefix: str = "") -> NormalizedTarget:
    """Limpia el numero marcado y aplica el prefijo de salida configurado."""
    raw = (raw or "").strip()
    if not raw:
        return NormalizedTarget("", "", False, "Introduce un numero o extension.")

    compact = re.sub(r"[\s().\-]", "", raw)
    if not ALLOWED_TARGET_CHARS.match(compact):
        invalid = sorted({char for char in compact if not ALLOWED_TARGET_CHARS.match(char)})
        return NormalizedTarget("", "", False, f"Caracteres no validos: {' '.join(invalid)}")
    if len(compact) > MAX_TARGET_LENGTH:
        return NormalizedTarget("", "", False, "El numero es demasiado largo.")

    prefix = re.sub(r"[\s().\-]", "", outbound_prefix or "")
    if compact.startswith("+"):
        return NormalizedTarget(compact, compact, False)

    if prefix and not compact.startswith(prefix):
        compact = f"{prefix}{compact}"
    return NormalizedTarget(compact, compact, compact.isdigit() and len(compact) <= EXTENSION_LENGTH)


@dataclass
class CallSession:
    target: str = ""
    peer: str = ""
    state: CallState = CallState.IDLE
    detail: str = ""
    started_at: float | None = None
    answered_at: float | None = None
    duration: int = 0
    muting: bool = False
    on_hold: bool = False
    log_record_id: int | None = None

    @property
    def active(self) -> bool:
        return self.state in (
            CallState.INCOMING,
            CallState.DIALING,
            CallState.RINGING,
            CallState.ANSWERED,
            CallState.CONNECTED,
            CallState.HOLDING,
        )


EventCallback = Callable[[CallSession], None]


class CallEngine:
    """Interfaz que implementara el motor INVITE + RTP."""

    def __init__(self, on_event: EventCallback | None = None) -> None:
        self.session = CallSession()
        self._on_event = on_event

    def _publish(self) -> None:
        if self._on_event is not None:
            self._on_event(self.session)

    @property
    def state(self) -> CallState:
        return self.session.state

    def start_call(self, target: str, account: AccountConfig, password: str) -> CallSession:
        raise NotImplementedError

    def answer(self) -> None:
        raise NotImplementedError

    def hangup(self) -> None:
        raise NotImplementedError

    def send_dtmf(self, digits: str) -> None:
        raise NotImplementedError

    def set_mute(self, enabled: bool) -> None:
        raise NotImplementedError

    def set_hold(self, enabled: bool) -> None:
        raise NotImplementedError


class PendingCallEngine(CallEngine):
    """Motor provisional: valida el destino y explica que falta el motor real."""

    def __init__(self, on_event: EventCallback | None = None) -> None:
        super().__init__(on_event)
        self.account: AccountConfig | None = None

    def start_call(self, target: str, account: AccountConfig, password: str) -> CallSession:
        normalized = normalize_target(target, account.outbound_prefix)
        if normalized.error:
            self.session = CallSession(state=CallState.FAILED, detail=normalized.error)
            self._publish()
            return self.session

        self.account = account
        self.session = CallSession(
            target=normalized.number,
            peer=normalized.display,
            state=CallState.UNSUPPORTED,
            detail=(
                f"Destino valido: {normalized.display}. La senalizacion INVITE, "
                "el audio RTP y el microfono se habilitan en la siguiente fase."
            ),
        )
        self._publish()
        return self.session

    def hangup(self) -> None:
        if self.session.state is not CallState.IDLE:
            self.session = CallSession(state=CallState.IDLE)
            self._publish()

    def send_dtmf(self, digits: str) -> None:
        return None

    def set_mute(self, enabled: bool) -> None:
        self.session.muting = enabled
        self._publish()

    def set_hold(self, enabled: bool) -> None:
        self.session.on_hold = enabled
        self._publish()
