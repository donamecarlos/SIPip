"""Configuracion no sensible persistida en el directorio de la app."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_DIR_NAME = "SIPip"
CONFIG_FILE_NAME = "config.json"


@dataclass
class AccountConfig:
    """Datos de la cuenta SIP. La contrasena se guarda aparte en storage.py."""

    server_host: str = ""
    server_port: int = 5060
    extension: str = ""
    domain: str = ""
    display_name: str = ""
    transport: str = "udp"
    expires: int = 3600
    auto_register: bool = False
    keep_password: bool = True
    outbound_prefix: str = ""
    history_days: int = 90
    window_geometry: str = ""
    sip_local_port: int = 0
    first_run_complete: bool = False

    def __post_init__(self) -> None:
        self.server_port = int(self.server_port)
        self.expires = max(60, min(int(self.expires), 86400))
        if not self.domain:
            self.domain = self.server_host
        if not self.display_name:
            self.display_name = self.extension

    @property
    def is_complete(self) -> bool:
        return bool(self.server_host and self.extension)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.server_host.strip():
            errors.append("El servidor es obligatorio (IP o dominio).")
        if not self.extension.strip():
            errors.append("La extension es obligatoria.")
        if not 1 <= self.server_port <= 65535:
            errors.append("El puerto debe estar entre 1 y 65535.")
        if self.transport.lower() not in ("udp", "tcp", "tls"):
            errors.append("Transporte no soportado: usa UDP, TCP o TLS.")
        return errors


def config_dir() -> Path:
    """Directorio de datos de la app, portable entre plataformas.

    Android usa el directorio privado de la aplicacion (python-for-android),
    Windows %APPDATA% y el resto un oculto en el home.
    """
    try:
        from android.storage import app_storage_path  # type: ignore[attr-defined]

        return Path(app_storage_path()) / APP_DIR_NAME
    except Exception:
        pass
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME
    return Path.home() / ".sipip"


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def load_config() -> AccountConfig:
    path = config_path()
    if not path.exists():
        return AccountConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AccountConfig()
    if not isinstance(raw, dict):
        return AccountConfig()

    known = {field.name for field in fields(AccountConfig)}
    values = {key: value for key, value in raw.items() if key in known}
    try:
        return AccountConfig(**values)
    except (TypeError, ValueError):
        return AccountConfig()


def save_config(config: AccountConfig) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    path = config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
