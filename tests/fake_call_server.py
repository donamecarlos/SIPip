"""Servidor de llamadas falso (INVITE/ACK/CANCEL/BYE) con eco RTP.

Reproduce el flujo minimo de Asterisk para pruebas sin infraestructura:
challenge digest, 100/180, 200 OK con SDP, y devuelve los paquetes de voz
recibidos por su socket RTP (produce siempre silencio G.711).
"""

from __future__ import annotations

import socket
import threading
import time

from sipclient.digest import compute_response, parse_auth_header
from sipclient.messages import SipParseError, parse_message
from .fake_socket import FakeServerSocket

REALM = "asterisk"
NONCE = "1700000000:fakecall98765432"


class FakeCallServer:
    def __init__(self, password: str = "secreto123", ringing: bool = True,
                 ringing_delay: float = 0.0, answer: bool = True,
                 transport: str = "udp") -> None:
        self.password = password
        self.ringing = ringing
        self.ringing_delay = ringing_delay
        self.answer = answer

        self.received: list[bytes] = []
        self.invites = 0
        self.ack_count = 0
        self.byes = 0
        self.cancels = 0
        self.bad_digest = 0
        self.rtp_received: list[bytes] = []

        self._socket: FakeServerSocket = FakeServerSocket(transport)
        self._rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rtp.bind(("127.0.0.1", 0))
        self._rtp.settimeout(0.1)

        self._running = False
        self._thread: threading.Thread | None = None
        self._rtp_thread: threading.Thread | None = None
        self._media_peer: tuple[str, int] | None = None
        self._authenticated: set[str] = set()
        self._active_cseq: str = ""
        self._challenge = f'Digest realm="{REALM}", nonce="{NONCE}", algorithm=MD5'

    @property
    def host(self) -> str:
        return self._socket.host

    @property
    def port(self) -> int:
        return self._socket.port

    @property
    def rtp_port(self) -> int:
        return int(self._rtp.getsockname()[1])

    def start(self) -> None:
        self._running = True
        self._socket.start()
        self._thread = threading.Thread(target=self._serve, name="fake-call", daemon=True)
        self._rtp_thread = threading.Thread(target=self._serve_rtp, name="fake-call-rtp", daemon=True)
        self._thread.start()
        self._rtp_thread.start()

    def stop(self) -> None:
        self._running = False
        for thread in (self._thread, self._rtp_thread):
            if thread is not None:
                thread.join(timeout=2.0)
        try:
            self._socket.close()
        except OSError:
            pass
        try:
            self._rtp.close()
        except OSError:
            pass

    def wait_for(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------------
    # SIP
    # ------------------------------------------------------------------

    def _serve(self) -> None:
        while self._running:
            try:
                data, peer = self._socket.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            self.received.append(data)
            try:
                request = parse_message(data)
            except SipParseError:
                continue
            if not request.is_request:
                continue
            method = request.method
            if method in ("ACK", "BYE", "CANCEL"):
                self._handle_control(method, request, peer)
            elif method == "INVITE":
                self._handle_invite(request, peer)

    def _handle_invite(self, request, peer: tuple[str, int]) -> None:
        call_id = request.header("call-id") or ""
        authorization = request.header("authorization")
        if call_id not in self._authenticated:
            if not authorization:
                lines = ["SIP/2.0 401 Unauthorized",
                         f"Via: {request.header('via')}",
                         f"To: {request.header('to')}",
                         f"From: {request.header('from')}",
                         f"Call-ID: {request.header('call-id')}",
                         f"CSeq: {request.header('cseq')}",
                         f"WWW-Authenticate: {self._challenge}",
                         "Content-Length: 0"]
                message = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
                try:
                    self._socket.sendto(message, peer)
                except OSError:
                    pass
                return
            if not self._validate(authorization, method="INVITE", uri=request.start_target):
                self.bad_digest += 1
                self._send("SIP/2.0 403 Forbidden", request, peer)
                return
            self._authenticated.add(call_id)

        is_reinvite = request.header("cseq", "1").split()[0] != str(self._active_cseq) and bool(self._active_cseq)
        self._active_cseq = request.header("cseq", "1").split()[0]
        self.invites += 1

        if not is_reinvite:
            self._send("SIP/2.0 100 Trying", request, peer)
            if self.ringing:
                self._send("SIP/2.0 180 Ringing", request, peer)
                if self.ringing_delay > 0:
                    time.sleep(self.ringing_delay)

        sip_body = self._offer_media(request.body)
        if not self.answer:
            return
        if sip_body:
            self._media_peer = sip_body
        self._send(
            "SIP/2.0 200 OK", request, peer,
            body=self._answer_sdp(request.body),
            contact=f"<sip:50@{self.host}:{self.port}>",
            to_tag="as7c9d8f",
        )

    def _handle_control(self, method: str, request, peer: tuple[str, int]) -> None:
        if method == "ACK":
            self.ack_count += 1
            return
        if method == "BYE":
            self.byes += 1
            self._send("SIP/2.0 200 OK", request, peer)
            return
        if method == "CANCEL":
            self.cancels += 1
            self._send("SIP/2.0 200 OK", request, peer)
            self._send("SIP/2.0 487 Request Terminated", request, peer)
            return

    def _validate(self, authorization: str, method: str, uri: str) -> bool:
        params = parse_auth_header(authorization)
        expected = compute_response(
            username=params.get("username", ""),
            password=self.password,
            realm=params.get("realm", ""),
            nonce=params.get("nonce", ""),
            method=method,
            uri=uri,
        )
        return params.get("response") == expected

    def _offer_media(self, body: bytes) -> tuple[str, int] | None:
        ip = ""
        port = 0
        for line in body.decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if parts and parts[0] == "c=IN" and len(parts) >= 3:
                ip = parts[2]
            elif parts and parts[0].startswith("m=audio") and len(parts) >= 2:
                try:
                    port = int(parts[1])
                except ValueError:
                    port = 0
        return (ip, port) if ip and port else None

    def _answer_sdp(self, request_body: bytes) -> bytes:
        lines = [
            "v=0",
            "o=- 1 1 IN IP4 127.0.0.1",
            "s=Asterisk",
            "c=IN IP4 127.0.0.1",
            "t=0 0",
            f"m=audio {self.rtp_port} RTP/AVP 0 101",
            "a=rtpmap:0 PCMU/8000",
            "a=rtpmap:101 telephone-event/8000",
            "a=sendrecv",
            "",
            "",
        ]
        return ("\r\n".join(lines)).encode("utf-8")

    def _send(self, start_line: str, request, peer: tuple[str, int],
              body: bytes = b"", contact: str = "", to_tag: str = "") -> None:
        to_header = request.header("to") or ""
        from_header = request.header("from") or ""
        local_tag = f";tag={to_tag}" if to_tag else ""
        lines = [start_line]
        lines.append(f"Via: {request.header('via')}")
        lines.append(f"To: {from_header}{local_tag}")
        lines.append(f"From: {to_header}")
        lines.append(f"Call-ID: {request.header('call-id')}")
        lines.append(f"CSeq: {request.header('cseq')}")
        if contact:
            lines.append(f"Contact: {contact}")
        lines.append(f"Content-Length: {len(body)}")
        message = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body
        try:
            self._socket.sendto(message, peer)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # RTP echo
    # ------------------------------------------------------------------

    def _serve_rtp(self) -> None:
        while self._running:
            try:
                data, peer = self._rtp.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            self.rtp_received.append(data)
            target = self._media_peer
            if target:
                try:
                    self._rtp.sendto(data, target)
                except OSError:
                    pass