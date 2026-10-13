"""Diagnostico de registro SIP desde linea de ordenes.

Ejemplo:
    python tools/check_sip.py --server 10.0.0.5 --extension 100
    set SIP_PASSWORD=clave123 && python tools/check_sip.py --server sip.empresa.local -e 100
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sipclient.config import AccountConfig  # noqa: E402
from sipclient.registrar import Registrar, RegistrarState  # noqa: E402

LABELS = {
    RegistrarState.IDLE: "IDLE",
    RegistrarState.REGISTERING: "REG",
    RegistrarState.CHALLENGED: "AUTH",
    RegistrarState.REGISTERED: "OK",
    RegistrarState.RETRYING: "RETRY",
    RegistrarState.FAILED: "FAIL",
    RegistrarState.STOPPED: "STOP",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Comprueba el registro SIP contra Issabel/Asterisk.")
    parser.add_argument("--server", "-s", required=True, help="IP o dominio del servidor SIP")
    parser.add_argument("--port", "-p", type=int, default=5060)
    parser.add_argument("--extension", "-e", default="", help="numero de extension")
    parser.add_argument("--password", default="", help="contrasena (preferible la variable SIP_PASSWORD)")
    parser.add_argument("--domain", "-d", default="", help="dominio SIP; por defecto el servidor")
    parser.add_argument("--expires", type=int, default=3600)
    parser.add_argument("--wait", type=float, default=15.0, help="segundos de espera maxima")
    args = parser.parse_args()

    password = args.password or os.environ.get("SIP_PASSWORD") or getpass.getpass("Contrasena: ")
    account = AccountConfig(
        server_host=args.server,
        server_port=args.port,
        extension=args.extension or getpass.getpass("Extension: "),
        domain=args.domain,
        expires=args.expires,
    )

    errors = account.validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    stamp = time.strftime("%H:%M:%S")
    print(f"{stamp} objetivo {account.server_host}:{account.server_port} "
          f"extension {account.extension} dominio {account.domain}")

    def on_event(state: RegistrarState, detail: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} [{LABELS[state]:5}] {detail}", flush=True)

    registrar = Registrar(account, password, on_event=on_event)
    try:
        registrar.start()
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    deadline = time.monotonic() + args.wait
    exit_code = 1
    try:
        while time.monotonic() < deadline:
            if registrar.is_registered():
                remaining = registrar.expires_remaining()
                print(f"\nResultado: REGISTRADO. Expira en {remaining}s.")
                exit_code = 0
                break
            if registrar.state is RegistrarState.FAILED:
                print(f"\nResultado: FALLO. {registrar.detail}", file=sys.stderr)
                break
            time.sleep(0.1)
        else:
            print(f"\nResultado: SIN RESPUESTA tras {args.wait}s.", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        if exit_code == 0:
            print("Enviando baja (expires=0)...")
            registrar.unregister()
            time.sleep(0.5)
        registrar.stop(timeout=2.0)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
