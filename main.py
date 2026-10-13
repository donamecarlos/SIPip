"""Punto de entrada de SIPip.

En Windows arranca la interfaz tkinter; en Android (python-for-android)
arranca la interfaz Kivy reutilizando el mismo nucleo SIP.
"""

from __future__ import annotations

import sys


def _is_android() -> bool:
    try:
        import android  # type: ignore[attr-defined]  # noqa: F401

        return True
    except Exception:
        return False


def main() -> int:
    if _is_android():
        from sipdroid.app import SIPipApp

        SIPipApp().run()
        return 0
    if sys.platform != "win32":
        print("SIPip esta en desarrollo solo para Windows.", file=sys.stderr)
        return 2
    from sipclient import __version__
    from sipclient.ui import run

    print(f"SIPip {__version__}")
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())