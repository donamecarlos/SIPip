"""Traza opcional del trafico SIP para depurar fallos de llamada.

Se activa con la variable de entorno SIPIP_TRACE=1 (o yes/true/on). Los
datagramas SIP enviados y recibidos se anexan a
%APPDATA%\\SIPip\\sip_trace.log para poder revisar paso a paso por que una
llamada falla (401/407, 403, 404, 480, 486, 503, reenvios de INVITE, etc).

Nunca lanza excepciones: si la traza falla (carpeta bloqueada, etc.) la app
sigue funcionando igual.
"""

from __future__ import annotations

import os
import threading
import time

from .config import config_dir

_ENABLED = os.environ.get("SIPIP_TRACE", "0").strip().lower() in (
    "1", "true", "yes", "on",
)
_LOCK = threading.Lock()
_MAX_BYTES = 1_000_000


def enabled() -> bool:
    return _ENABLED


def write(direction: str, peer: str, data: bytes) -> None:
    """Anade un datagrama al log de traza (best effort)."""
    if not _ENABLED:
        return
    try:
        text = data.decode("utf-8", errors="replace").rstrip()
    except (TypeError, ValueError):
        return
    try:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "sip_trace.log"
        stamp = time.strftime("%H:%M:%S")
        with _LOCK:
            try:
                if path.stat().st_size > _MAX_BYTES:
                    path.unlink()
            except OSError:
                pass
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"===== {stamp} {direction} [{peer}]\n")
                handle.write(text)
                handle.write("\n\n")
    except OSError:
        pass