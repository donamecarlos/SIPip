"""Llamante falso: envia un INVITE y completa el dialogo hacia el listener.

Reproduce el flujo minimo de un llamante real para probar la recepcion de
llamadas entrantes del motor: INVITE con SDP, 100/180, 200 OK, ACK y eco RTP.
Puede cancelar (CANCEL), colgar (BYE) o permanecer sonando hasta que el motor
responda o rechace.
"""

from __future__ import annotations

import socket
import threading
import time

from sipclient.messages import SipParseError, parse_message, random_token
from .fake_socket import FakeClientSocket

SDP = [
    "v=0",
    "o=fake 1 1 IN IP4 127.0.0.1",
    "s=FakeIncoming",
    "c=IN IP4 127.0.0.1",
    "t=0 0",
    "m=audio {rtp_port} RTP/AVP 0 101",
    "a=rtpmap:0 PCMU/8000",
    "a=rtpmap:101 telephone-event/8000",
    "a=sendrecv",
]


class FakeIncomingCaller:
    def __init__(self, display: str = "Llamante", username: str = "200",
                 transport: str = "udp") -> None:
        self.display = display
        self.username = username
        self._tag = random_token(8)

        self._socket: FakeClientSocket = FakeClientSocket(transport)
        self._transport = transport
        self._rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rtp.bind(("127.0.0.1", 0))
        self._rtp.settimeout(0.1)

        self.received: list[bytes] = []
        self.statuses: list[int] = []
        self.acks = 0
        self.byes = 0
        self.bye_confirms = 0
        self.cancels = 0
        self.rtp_received: list[bytes] = []

        self._running = False
        self._thread: threading.Thread | None = None
        self._rtp_thread: threading.Thread | None = None
        self._listener: tuple[str, int] | None = None
        self._number = ""
        self._username = username
        self._call_id = ""
        self._uri = ""
        self._cseq = 1
        self._engine_tag = ""
        self._media_peer: tuple[str, int] | None = None
        self._established = False
        self._ack_sent = False

    @property
    def host(self) -> str:
        return self._socket.host

    @property
    def port(self) -> int:
        return self._socket.port

    @property
    def rtp_port(self) -> int:
        return int(self._rtp.getsockname()[1])

    @property
    def established(self) -> bool:
        return self._established

    @property
    def rejected(self) -> bool:
        return any(status >= 400 for status in self.statuses)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._serve, name="fake-incoming", daemon=True)
        self._rtp_thread = threading.Thread(target=self._serve_rtp, name="fake-incoming-rtp", daemon=True)
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
    # Flujo de llamada
    # ------------------------------------------------------------------

    def call(self, number: str, listener: tuple[str, int]) -> None:
        """Envia un INVITE al listener y deja que el hilo complete el dialogo."""
        self._listener = tuple(listener)
        self._number = number
        self._uri = f"sip:{number}@{listener[0]}:{listener[1]}"
        self._call_id = f"{random_token(12)}@{self.host}"
        self._cseq = 1
        self._engine_tag = ""
        self._media_peer = None
        self._established = False
        self._ack_sent = False
        self.statuses.clear()
        if self._transport == "tcp":
            self._socket.connect_to(self._listener)
        self._socket.sendto(self._build_invite(), self._listener)

    def _build_invite(self) -> bytes:
        body = self._offer_sdp()
        lines = [
            f"INVITE {self._uri} SIP/2.0",
            f"Via: SIP/2.0/{self._transport.upper()} {self.host}:{self.port};branch=z9hG4bK{self._tag};rport",
            "Max-Forwards: 70",
            f'From: "{self.display}" <sip:{self._username}@{self.host}>;tag={self._tag}',
            f"To: <sip:{self._number}@{self._listener[0]}:{self._listener[1]}>",
            f"Call-ID: {self._call_id}",
            f"CSeq: {self._cseq} INVITE",
            "User-Agent: FakeIncoming/1.0",
            f"Contact: <sip:{self._username}@{self.host}:{self.port}>",
            "Content-Type: application/sdp",
            f"Content-Length: {len(body)}",
            "",
            "",
        ]
        return ("\r\n".join(lines)).encode("utf-8") + body

    def _offer_sdp(self) -> bytes:
        return ("\r\n".join(SDP).format(rtp_port=self.rtp_port) + "\r\n").encode("utf-8")

    def _serve(self) -> None:
        while self._running:
            try:
                data, peer = self._socket.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            self.received.append(data)
            try:
                message = parse_message(data)
            except SipParseError:
                continue
            if message.is_request:
                if message.method == "BYE":
                    self.bye_confirms += 1
                    self._send_response(message, 200, "OK", peer)
                continue
            status = message.status or 0
            self.statuses.append(status)
            if status == 200 and not self._ack_sent:
                self._ack_sent = True
                self._established = True
                self._engine_tag = self._response_tag(message.header("to") or "")
                media = self._parse_media_peer(message.body)
                if media:
                    self._media_peer = media
                self._send_ack(peer)

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

    def cancel(self) -> None:
        """CANCEL mientras suena (mismo dialog; el motor no valida el branch)."""
        lines = [
            f"CANCEL {self._uri} SIP/2.0",
            f"Via: SIP/2.0/{self._transport.upper()} {self.host}:{self.port};branch=z9hG4bK{self._tag};rport",
            "Max-Forwards: 70",
            f'From: "{self.display}" <sip:{self._username}@{self.host}>;tag={self._tag}',
            f"To: <sip:{self._number}@{self._listener[0]}:{self._listener[1]}>;tag={self._engine_tag}",
            f"Call-ID: {self._call_id}",
            f"CSeq: {self._cseq} CANCEL",
            "User-Agent: FakeIncoming/1.0",
            "Content-Length: 0",
            "",
            "",
        ]
        self._socket.sendto(("\r\n".join(lines)).encode("utf-8"), self._listener)
        self.cancels += 1

    def bye(self) -> None:
        """BYE en el dialogo establecido."""
        if not self._established:
            return
        self._cseq += 1
        lines = [
            f"BYE {self._uri} SIP/2.0",
            f"Via: SIP/2.0/{self._transport.upper()} {self.host}:{self.port};branch=z9hG4bK{random_token(16)};rport",
            "Max-Forwards: 70",
            f'From: "{self.display}" <sip:{self._username}@{self.host}>;tag={self._tag}',
            f"To: <sip:{self._number}@{self._listener[0]}:{self._listener[1]}>;tag={self._engine_tag}",
            f"Call-ID: {self._call_id}",
            f"CSeq: {self._cseq} BYE",
            "User-Agent: FakeIncoming/1.0",
            "Content-Length: 0",
            "",
            "",
        ]
        self._socket.sendto(("\r\n".join(lines)).encode("utf-8"), self._listener)
        self.byes += 1

    # ------------------------------------------------------------------
    # Construccion de mensajes
    # ------------------------------------------------------------------

    def _send_ack(self, peer: tuple[str, int]) -> None:
        lines = [
            f"ACK {self._uri} SIP/2.0",
            f"Via: SIP/2.0/{self._transport.upper()} {self.host}:{self.port};branch=z9hG4bK{random_token(16)};rport",
            "Max-Forwards: 70",
            f'From: "{self.display}" <sip:{self._username}@{self.host}>;tag={self._tag}',
            f"To: <sip:{self._number}@{self._listener[0]}:{self._listener[1]}>;tag={self._engine_tag}",
            f"Call-ID: {self._call_id}",
            f"CSeq: {self._cseq} ACK",
            "User-Agent: FakeIncoming/1.0",
            "Content-Length: 0",
            "",
            "",
        ]
        self._socket.sendto(("\r\n".join(lines)).encode("utf-8"), peer)
        self.acks += 1

    def _send_response(self, request, status: int, reason: str, peer: tuple[str, int]) -> None:
        to_header = request.header("to") or ""
        from_header = request.header("from") or ""
        local_tag = f";tag={self._tag}" if status == 200 else ""
        lines = [f"SIP/2.0 {status} {reason}".rstrip()]
        lines.append(f"Via: {request.header('via')}")
        lines.append(f"To: {from_header}{local_tag}")
        lines.append(f"From: {to_header}")
        lines.append(f"Call-ID: {request.header('call-id')}")
        lines.append(f"CSeq: {request.header('cseq')}")
        lines.append("Content-Length: 0")
        payload = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        try:
            self._socket.sendto(payload, peer)
        except OSError:
            pass

    @staticmethod
    def _response_tag(to_header: str) -> str:
        tags = [chunk.strip()[4:] for chunk in to_header.split(";")[1:] if chunk.strip().startswith("tag=")]
        return tags[-1] if tags else ""

    @staticmethod
    def _parse_media_peer(body: bytes) -> tuple[str, int] | None:
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
