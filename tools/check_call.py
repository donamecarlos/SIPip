"""Diagnostico de llamada SIP (INVITE) desde linea de ordenes.

Reproduce plano por plano el flujo de una llamada saliente contra el servidor
SIP y muestra cada respuesta para localizar por que falla (401/407, 403, 404,
480, 486, 503, timeout, etc).

Ejemplo:
    set SIP_PASSWORD=clave123 && python tools/check_call.py -s 10.0.0.5 -e 100 --destino 300
"""

from __future__ import annotations

import argparse
import getpass
import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sipclient.config import AccountConfig  # noqa: E402
from sipclient.digest import DigestAuthenticator  # noqa: E402
from sipclient.messages import parse_message, random_token  # noqa: E402
from sipclient.sdp import build_offer, parse_answer  # noqa: E402
from sipclient.transport import UdpTransport  # noqa: E402

MAX_SENDS = 4
T1 = 0.5


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _local_endpoint() -> tuple[str, int]:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("0.0.0.0", 0))
        ip, port = probe.getsockname()[:2]
        # El destino esta conectado en UdpTransport; tambien hay que predecir la
        # IP que vera el servidor. Usamos el mismo truco que el cliente: un
        # socket UDP conectado a un host cualquiera descubre la via de salida.
        probe2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe2.connect(("8.8.8.8", 80))
        ip = probe2.getsockname()[0]
        probe2.close()
        return str(ip), int(port)
    except OSError:
        pass
    finally:
        try:
            probe.close()
        except OSError:
            pass
    return socket.gethostbyname(socket.gethostname()), 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Comprueba una llamada saliente SIP.")
    parser.add_argument("--server", "-s", required=True, help="IP o dominio del servidor SIP")
    parser.add_argument("--port", "-p", default=5060)
    parser.add_argument("--extension", "-e", default="", help="numero de extension")
    parser.add_argument("--password", default="", help="contrasena (preferible SIP_PASSWORD)")
    parser.add_argument("--domain", "-d", default="", help="dominio SIP; por defecto el servidor")
    parser.add_argument("--destino", "-n", required=True, help="numero o extension a llamar")
    parser.add_argument("--timeout", type=float, default=25.0, help="segundos de espera maxima")
    args = parser.parse_args()

    password = args.password or os.environ.get("SIP_PASSWORD") or getpass.getpass("Contrasena: ")
    extension = args.extension or getpass.getpass("Extension: ")
    account = AccountConfig(
        server_host=args.server,
        server_port=_int(args.port, 5060),
        extension=extension,
        domain=args.domain,
        expires=3600,
    )
    errors = account.validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    domain = account.domain or account.server_host
    destino = args.destino
    stamp = time.strftime("%H:%M:%S")
    print(f"{stamp} INVITE sip:{destino}@{domain} -> {account.server_host}:{account.server_port}")
    print(f"{stamp} extension {account.extension} ({account.display_name or account.extension})")

    try:
        transport = UdpTransport(account.server_host, account.server_port)
    except OSError as exc:
        print(f"ERROR: no se pudo abrir el socket UDP: {exc}", file=sys.stderr)
        return 2

    local_ip_media, media_port = _local_endpoint()
    local_ip, local_port = transport.local_address
    call_id = f"{random_token(20)}@sipip"
    tag = random_token(10)
    branch = f"z9hG4bK{random_token(20)}"
    auth = DigestAuthenticator(extension, password)
    body = build_offer(local_ip_media, media_port)
    requested_uri = f"sip:{destino}@{domain}"
    cseq = 1

    def build_invite() -> bytes:
        headers = [
            ("Via", f"SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport"),
            ("Max-Forwards", "70"),
            ("From", f'"{account.display_name or extension}" <sip:{extension}@{domain}>;tag={tag}'),
            ("To", f"<{requested_uri}>"),
            ("Call-ID", call_id),
            ("CSeq", f"{cseq} INVITE"),
            ("Contact", f"<sip:{extension}@{local_ip}:{local_port}>"),
            ("Content-Type", "application/sdp"),
            ("User-Agent", "SIPip-check"),
            ("Allow", "INVITE, ACK, CANCEL, BYE, OPTIONS, NOTIFY, MESSAGE, INFO"),
        ]
        if auth.challenged:
            headers.append(("Authorization", auth.build_header("INVITE", requested_uri)))
        lines = [f"INVITE {requested_uri} SIP/2.0"]
        lines.extend(f"{name}: {value}" for name, value in headers)
        payload = body.encode("utf-8")
        lines.append(f"Content-Length: {len(payload)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload

    def build_ack() -> bytes:
        return ("\r\n".join([
            f"ACK {requested_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{extension}@{domain}>;tag={tag}",
            f"To: <{requested_uri}>",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} ACK",
            "Content-Length: 0",
        ]) + "\r\n\r\n").encode("utf-8")

    def build_bye() -> bytes:
        return ("\r\n".join([
            f"BYE {requested_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{extension}@{domain}>;tag={tag}",
            f"To: <{requested_uri}>;tag={peer_tag}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} BYE",
            "Content-Length: 0",
        ]) + "\r\n\r\n").encode("utf-8")

    peer_tag = ""
    exit_code = 1
    try:
        transport.send(build_invite())
        print(f"{stamp} -> INVITE ({len(body)} bytes SDP)")

        started = time.monotonic()
        retransmits = 1
        timer = T1
        challenge_attempts = 0
        answered = False

        while time.monotonic() - started < args.timeout:
            remaining = args.timeout - (time.monotonic() - started)
            data = transport.recv(time.monotonic() + min(remaining, 0.25))
            if data is None:
                if retransmits < MAX_SENDS:
                    retransmits += 1
                    transport.send(build_invite())
                    print(f"{stamp} -> {build_invite().decode().splitlines()[0]} (reenvio {retransmits})")
                    timer = min(timer * 2, 4.0)
                continue

            try:
                message = parse_message(data)
            except Exception as exc:  # noqa: BLE001
                print(f"{stamp} << datagrama no parseable: {exc}")
                continue

            head, _, _ = data.decode("utf-8", errors="replace").partition("\r\n\r\n")
            print(f"{stamp} << {head}")
            if message.is_request:
                continue

            status = message.status or 0
            if 100 <= status < 200:
                print(f"{stamp}    provisional ({status})")
                continue

            if status in (401, 407):
                header = message.header("www-authenticate") or message.header(
                    "proxy-authenticate"
                )
                if not header:
                    print(f"{stamp} ERROR: reto {status} sin header de autenticacion")
                    break
                try:
                    auth.challenge_with(header)
                except ValueError as exc:
                    print(f"{stamp} ERROR: reto invalido: {exc}")
                    break
                challenge_attempts += 1
                if challenge_attempts >= 3:
                    print(f"{stamp} ERROR: credenciales rechazadas repetidamente")
                    break
                branch = f"z9hG4bK{random_token(20)}"
                transport.send(build_invite())
                print(f"{stamp} -> INVITE con Authorization (intento {challenge_attempts + 1})")
                retransmits = 1
                timer = T1
                continue

            peer_tag = (message.header("to") or "").split(";tag=")[-1] if ";tag=" in (message.header("to") or "") else ""

            if 200 <= status < 300:
                try:
                    media = parse_answer(message.body.decode("utf-8", errors="replace"))
                except Exception as exc:  # noqa: BLE001
                    print(f"{stamp} ERROR: SDP invalido: {exc}")
                    break
                print(f"{stamp} LLAMADA ACEPTADA: audio {media.codec} {media.connection_ip}:{media.port}")
                answered = True
                transport.send(build_ack())
                print(f"{stamp} -> ACK {requested_uri}")
                cseq += 1
                transport.send(build_bye())
                print(f"{stamp} -> BYE (cierre de prueba)")
                deadline = time.monotonic() + 3.0
                ok = False
                while time.monotonic() < deadline:
                    bye_data = transport.recv(deadline)
                    if bye_data is None:
                        continue
                    bye_message = parse_message(bye_data)
                    if not bye_message.is_request and (bye_message.status or 0) == 200:
                        ok = True
                        break
                print(f"{stamp} << BYE confirmado" if ok else f"{stamp} << sin confirmacion de BYE")
                exit_code = 0
                break

            transport.send(build_ack())
            print(f"{stamp} -> ACK (respuesta final)")

            reasons = {403: "credenciales rechazadas por el servidor",
                       404: "destino/extension no existe",
                       480: "destino temporalmente no disponible",
                       486: "destino ocupado",
                       503: "servicio no disponible ahora"}
            print(f"{stamp} RESULTADO: llamada RECHAZADA con {status} - {reasons.get(status, 'motivo del servidor')}")
            break
        else:
            print(f"{stamp} RESULTADO: SIN RESPUESTA tras {args.timeout}s (revisa firewall/IP)")
        if not answered and exit_code:
            print("Pista: activa SIPIP_TRACE=1 en la app y repite la llamada; el log va a "
                  "%APPDATA%\\SIPip\\sip_trace.log")
    except OSError as exc:
        print(f"ERROR de red: {exc}", file=sys.stderr)
    finally:
        transport.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())