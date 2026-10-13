"""Servidor REGISTER falso para pruebas, sin Issabel ni Asterisk."""

from __future__ import annotations

import socket
import threading

from sipclient.digest import compute_response
from sipclient.messages import SipParseError, parse_message
from .fake_socket import FakeServerSocket

REALM = "asterisk"
NONCE = "1700000000:abcdef0123456789"


class FakeRegistrarServer:
    """Responde 401 una vez y despues valida el digest y devuelve 200 OK."""

    def __init__(
        self,
        password: str = "secreto123",
        expires: int = 120,
        voicemail: int | None = None,
        transport: str = "udp",
    ) -> None:
        self.password = password
        self.expires = expires
        self.voicemail = voicemail
        self.received: list[bytes] = []
        self.registrations = 0
        self.deregistrations = 0
        self.bad_digest = 0

        self._socket: FakeServerSocket = FakeServerSocket(transport)
        self._thread: threading.Thread | None = None
        self._challenge_header = f'Digest realm="{REALM}", nonce="{NONCE}", algorithm=MD5'

    @property
    def port(self) -> int:
        return self._socket.port

    @property
    def host(self) -> str:
        return self._socket.host

    def start(self) -> None:
        self._running = True
        self._socket.start()
        self._thread = threading.Thread(target=self._serve, name="fake-registrar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._socket.close()

    def wait_for_registrations(self, count: int, timeout: float = 5.0) -> bool:
        return self._wait_for(lambda: self.registrations >= count, timeout)

    def wait_for_deregistrations(self, count: int, timeout: float = 5.0) -> bool:
        return self._wait_for(lambda: self.deregistrations >= count, timeout)

    def _wait_for(self, predicate, timeout: float) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def _serve(self) -> None:
        import time

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
            if not request.is_request or request.method != "REGISTER":
                continue

            authorization = request.header("authorization")
            if not authorization:
                response = (
                    "SIP/2.0 401 Unauthorized\r\n"
                    f'WWW-Authenticate: {self._challenge_header}\r\n'
                    f"To: {request.header('to')}\r\n"
                    f"From: {request.header('from')}\r\n"
                    f"Call-ID: {request.header('call-id')}\r\n"
                    f"CSeq: {request.header('cseq')}\r\n"
                    "Server: FakeAsterisk/1.0\r\n"
                    "Content-Length: 0\r\n\r\n"
                )
                self._socket.sendto(response.encode("utf-8"), peer)
                continue

            if not self._validate(authorization):
                self.bad_digest += 1
                response = (
                    "SIP/2.0 403 Forbidden\r\n"
                    f"To: {request.header('to')}\r\n"
                    f"From: {request.header('from')}\r\n"
                    f"Call-ID: {request.header('call-id')}\r\n"
                    f"CSeq: {request.header('cseq')}\r\n"
                    "Content-Length: 0\r\n\r\n"
                )
                self._socket.sendto(response.encode("utf-8"), peer)
                continue

            expires_header = request.header("expires", "3600")
            if expires_header.strip() == "0":
                self.deregistrations += 1
            else:
                self.registrations += 1
                time.sleep(0)

            response = (
                "SIP/2.0 200 OK\r\n"
                f"To: {request.header('to')};tag=fake\r\n"
                f"From: {request.header('from')}\r\n"
                f"Call-ID: {request.header('call-id')}\r\n"
                f"CSeq: {request.header('cseq')}\r\n"
                f"{request.header('contact')};expires={self.expires}\r\n"
                f"Expires: {self.expires}\r\n"
                f"{self._messages_waiting_header()}"
                "Server: FakeAsterisk/1.0\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            self._socket.sendto(response.encode("utf-8"), peer)

    def _messages_waiting_header(self) -> str:
        if self.voicemail is None:
            return ""
        if self.voicemail == 0:
            return "Messages-Waiting: no\r\n"
        return f"Messages-Waiting: yes;voicemail={self.voicemail}\r\n"

    def _validate(self, authorization: str) -> bool:
        from sipclient.digest import parse_auth_header

        params = parse_auth_header(authorization)
        expected = compute_response(
            username=params.get("username", ""),
            password=self.password,
            realm=params.get("realm", ""),
            nonce=params.get("nonce", ""),
            method="REGISTER",
            uri=params.get("uri", ""),
        )
        return params.get("response") == expected
