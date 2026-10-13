"""Permisos de primer uso: reglas de firewall y privacidad del microfono.

Las reglas de firewall requieren elevacion (UAC) porque se crean para el
exe de SIPip o para los puertos de senalizacion; los permisos del microfono
se gestionan en el registro de la sesion de Windows (HKCU, sin UAC).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FIREWALL_RULE_SIGNAL_UDP = "SIPip Signal UDP"
FIREWALL_RULE_SIGNAL_TCP = "SIPip Signal TCP"
FIREWALL_RULE_PROGRAM = "SIPip Program"

_MICROPHONE_KEY = (
    r"HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)

_KNOWN_RULE_NAMES = (
    FIREWALL_RULE_SIGNAL_UDP,
    FIREWALL_RULE_SIGNAL_TCP,
    FIREWALL_RULE_PROGRAM,
    "SIPip PBX UDP",  # regla equivalente aplicada en despliegues anteriores
)


def is_windows() -> bool:
    return os.name == "nt"


_cache_ttl = 5.0
_cache_data: dict[str, tuple[float, bool]] = {}


def _cache(key: str, value: bool) -> None:
    _cache_data[key] = (time.monotonic(), value)


def _cached(key: str) -> bool | None:
    stamp, value = _cache_data.get(key, (0.0, False))
    if time.monotonic() - stamp < _cache_ttl:
        return value
    return None


def _rule_exists(name: str) -> bool:
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return False
        lowered = result.stdout.lower()
        return any(marker in lowered for marker in ("enabled:", "habilitada:"))
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def firewall_rules_complete() -> bool:
    """True si ya hay alguna regla de SIPip activa en el firewall."""
    if not is_windows():
        return True
    cached = _cached("firewall")
    if cached is not None:
        return cached
    value = any(_rule_exists(name) for name in _KNOWN_RULE_NAMES)
    _cache("firewall", value)
    return value


def _runtime_exe() -> Path | None:
    raw = sys.executable
    if not raw or not os.path.isfile(raw):
        return None
    name = Path(raw).name.lower()
    if name.startswith("sipip") and name.endswith(".exe"):
        return Path(raw)
    if getattr(sys, "frozen", False) and name.endswith(".exe"):
        return Path(raw)
    return None


def _elevated_add_firewall_rules() -> bool:
    """Crea las reglas en un proceso elevado. Ejecutar en un hilo aparte."""
    exe = _runtime_exe()
    program_rule = ""
    if exe is not None:
        program_rule = (
            "& netsh advfirewall firewall add rule "
            f'name="{FIREWALL_RULE_PROGRAM}" dir=in action=allow program="{exe}" '
            "enable=yes profile=any | Out-Null\n"
        )

    marker = Path(tempfile.gettempdir()) / f"sipip_fw_{os.getpid()}.done"
    try:
        marker.unlink()
    except OSError:
        pass

    script = "\n".join(
        [
            "$ErrorActionPreference = 'Continue'",
            "& netsh advfirewall firewall add rule "
            f'name="{FIREWALL_RULE_SIGNAL_UDP}" dir=in action=allow protocol=UDP '
            "localport=5060 remoteip=192.168.0.0/16 profile=any | Out-Null",
            "& netsh advfirewall firewall add rule "
            f'name="{FIREWALL_RULE_SIGNAL_TCP}" dir=in action=allow protocol=TCP '
            "localport=5060 remoteip=192.168.0.0/16 profile=any | Out-Null",
            program_rule,
            f"'DONE' | Out-File -FilePath '{marker}' -Encoding ascii",
            "",
        ]
    )

    script_path = Path(tempfile.gettempdir()) / f"sipip_firewall_{os.getpid()}.ps1"
    try:
        script_path.write_text(script, encoding="utf-8")
    except OSError:
        return False
    try:
        command = (
            "Start-Process -FilePath 'powershell.exe' "
            "-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"
            f"\"{script_path}\" -Verb RunAs -Wait"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        try:
            script_path.unlink()
        except OSError:
            pass
        return False
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass
    return marker.exists()


def request_firewall_access() -> bool:
    """Pide crear las reglas (mostrando UAC). Devuelve True si se aplicaron."""
    if not is_windows() or firewall_rules_complete():
        return True
    return _elevated_add_firewall_rules()


def microphone_enabled() -> bool:
    """True si el microfono esta permitido en la privacidad de Windows."""
    if not is_windows():
        return True
    cached = _cached("microphone")
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            ["reg", "query", _MICROPHONE_KEY, "/v", "Value"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    value = ""
    for line in result.stdout.splitlines():
        if "REG_SZ" in line:
            value = line.rsplit("REG_SZ", 1)[1].strip().lower()
            break
    enabled = value in ("", "allow")
    _cache("microphone", enabled)
    return enabled


def allow_microphone() -> bool:
    """Activa el microfono en la privacidad de Windows (sin UAC)."""
    if not is_windows() or microphone_enabled():
        return True
    try:
        result = subprocess.run(
            ["reg", "add", _MICROPHONE_KEY, "/v", "Value", "/t", "REG_SZ", "/d", "Allow", "/f"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def open_microphone_settings() -> None:
    """Abre el asistente de privacidad de microfono de Windows."""
    if os.name == "nt":
        try:
            os.startfile("ms-settings:privacy-microphone")  # type: ignore[attr-defined]
        except OSError:
            pass


def status() -> dict[str, object]:
    return {
        "firewall": firewall_rules_complete(),
        "microphone": microphone_enabled(),
        "exe": str(_runtime_exe()) if _runtime_exe() else "",
    }