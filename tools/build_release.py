"""Empaqueta SIPip: construye el exe con PyInstaller y el instalador con Inno.

Uso:
    python tools/build_release.py [--version 0.2.0] [--no-exe] [--no-setup]

Salidas:
    dist/release/SIPip.exe            exe onefile (tambien copiado al instalador)
    dist/installer/SIPip-Setup-*.exe  instalador por usuario
    versiones de escritorio/SIPip.exe misma build que antes
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "build" / "SIPip.spec"
DIST = ROOT / "dist"
RELEASE = DIST / "release"
INSTALLER = DIST / "installer"
DESKTOP_COPY = ROOT / "versiones de escritorio"
ISS = ROOT / "installer" / "sipip.iss"


def find_iscc() -> str:
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    try:
        result = subprocess.run(
            ["where", "ISCC"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.splitlines()[0].strip()
    raise SystemExit("No se encontro ISCC.exe de Inno Setup 6.")


def build_exe() -> Path:
    RELEASE.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=ROOT,
    )
    built = DIST / "SIPip.exe"
    if not built.exists():
        raise SystemExit(f"No se genero el exe esperado: {built}")
    target = RELEASE / "SIPip.exe"
    shutil.copy2(built, target)
    DESKTOP_COPY.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, DESKTOP_COPY / "SIPip.exe")
    return target


def current_version() -> str:
    import ast

    init = (ROOT / "sipclient" / "__init__.py").read_text(encoding="utf-8")
    for line in init.splitlines():
        if line.startswith("__version__"):
            return ast.literal_eval(line.split("=", 1)[1].strip())
    return "0.2.0"


def build_setup(iscc: str, version: str) -> Path:
    INSTALLER.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["SIPIP_VERSION"] = version
    subprocess.check_call([iscc, str(ISS)], cwd=ROOT, env=environment)
    setup_path = INSTALLER / f"SIPip-Setup-{version}.exe"
    if not setup_path.exists():
        raise SystemExit(f"No se genero el instalador esperado: {setup_path}")
    return setup_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Empaqueta SIPip para Windows.")
    parser.add_argument("--version", default=None, help="Version a empaquetar.")
    parser.add_argument("--no-exe", action="store_true", help="No reconstruir el exe.")
    parser.add_argument("--no-setup", action="store_true", help="No compilar el instalador.")
    args = parser.parse_args()

    version = args.version or current_version()
    if not args.no_exe or not args.no_setup:
        print(f"Empaquetando SIPip {version}")

    if not args.no_exe:
        exe = build_exe()
        print(f"exe: {exe}")
    if not args.no_setup:
        iscc = find_iscc()
        setup = build_setup(iscc, version)
        print(f"instalador: {setup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())