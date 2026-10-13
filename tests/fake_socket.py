"""Sockets de prueba UDP/TCP con la API recvfrom/sendto usada por los fakes.

Permite ejecutar los servidores de prueba (registro, llamadas) y el llamante
falso tanto sobre UDP como sobre TCP sin tocar el codigo de sus bucles.
"""

from __future__ import annotations

import queue
import socket
import threading

from sipclient.transport import _frame_boundaries


class FakeServerSocket:
    """Listener UDP o TCP con API recvfrom/sendto.

    En TCP cada conexion entrante la lee un hilo propio que entrega los
    mensajes completos a una cola y sendto() los reenvia por la conexion
    correspondiente al peer.
    """

    def __init__(self, transport: str = "udp") -> None:
        self.transport = transport
        self._running = False
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[bytes, tuple[str, int]]] = queue.Queue()
        self._conns: dict[tuple[str, int], socket.socket] = {}
        self._threads: list[threading.Thread] = []

        self._socket = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
        )
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))

    @property
    def port(self) -> int:
        return int(self._socket.getsockname()[1])

    @property
    def host(self) -> str:
        return "127.0.0.1"

    def start(self) -> None:
        self._running = True
        if self.transport == "udp":
            self._socket.settimeout(0.2)
            return
        self._socket.settimeout(0.2)
        self._socket.listen(16)
        threading.Thread(
            target=self._accept_loop, name="fake-server-accept", daemon=True
        ).start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._socket.accept()
            except (socket.timeout, OSError):
                continue
            peer = (str(addr[0]), int(addr[1]))
            conn.settimeout(0.2)
            with self._lock:
                self._conns[peer] = conn
            thread = threading.Thread(
                target=self._read_loop, args=(conn, peer), name="fake-server-read", daemon=True
            )
            thread.start()
            with self._lock:
                self._threads.append(thread)

    def _read_loop(self, conn: socket.socket, peer: tuple[str, int]) -> None:
        buffer = bytearray()
        while self._running:
            boundary, content_length = _frame_boundaries(bytes(buffer))
            if boundary is not None and len(buffer) >= boundary + content_length:
                message = bytes(buffer[: boundary + content_length])
                del buffer[: boundary + content_length]
                self._queue.put((message, peer))
                continue
            conn.settimeout(0.2)
            try:
                chunk = conn.recv(65535)
            except (socket.timeout, OSError):
                continue
            if not chunk:
                break
            buffer += chunk
        try:
            conn.close()
        except OSError:
            pass

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        del size
        if self.transport == "udp":
            return self._socket.recvfrom(65535)
        try:
            return self._queue.get(timeout=0.2)
        except queue.Empty as exc:
            raise socket.timeout from exc

    def sendto(self, data: bytes, peer: tuple[str, int]) -> None:
        if self.transport == "udp":
            self._socket.sendto(data, peer)
            return
        key = (str(peer[0]), int(peer[1]))
        with self._lock:
            conn = self._conns.get(key)
        if conn is None:
            raise OSError("Sin conexion con el peer")
        conn.sendall(data)

    def close(self) -> None:
        self._running = False
        with self._lock:
            conns = list(self._conns.values())
            threads = list(self._threads)
            self._conns.clear()
            self._threads.clear()
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._socket.close()
        except OSError:
            pass
        for thread in threads:
            thread.join(timeout=1.0)


class FakeClientSocket:
    """Cliente UDP o TCP con API recvfrom/sendto.

    En TCP el envio va por la conexion establecida con connect_to() y la
    recepcion reconstruye los mensajes completos por Content-Length.
    """

    def __init__(self, transport: str = "udp") -> None:
        self.transport = transport
        self._buffer = bytearray()
        if transport == "tcp":
            self._conn: socket.socket | None = None
            return
        self._conn = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._conn.bind(("127.0.0.1", 0))
        self._conn.settimeout(0.1)

    @property
    def port(self) -> int:
        return int(self._conn.getsockname()[1])

    @property
    def host(self) -> str:
        return "127.0.0.1"

    def connect_to(self, address: tuple[str, int]) -> None:
        if self.transport != "tcp":
            return
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.connect(address)
        conn.settimeout(0.1)
        old = self._conn
        self._conn = conn
        self._buffer.clear()
        if old is not None:
            try:
                old.close()
            except OSError:
                pass

    def sendto(self, data: bytes, peer: tuple[str, int]) -> None:
        if self.transport == "udp":
            self._conn.sendto(data, peer)
            return
        self._conn.sendall(data)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        del size
        if self.transport == "udp":
            return self._conn.recvfrom(65535)
        if self._conn is None:
            raise socket.timeout
        while True:
            boundary, content_length = _frame_boundaries(bytes(self._buffer))
            if boundary is not None and len(self._buffer) >= boundary + content_length:
                message = bytes(self._buffer[: boundary + content_length])
                del self._buffer[: boundary + content_length]
                return message, ("127.0.0.1", 0)
            try:
                chunk = self._conn.recv(65535)
            except (socket.timeout, OSError):
                raise socket.timeout from None
            if not chunk:
                raise socket.timeout from None
            self._buffer += chunk

    def close(self) -> None:
        try:
            self._conn.close()
        except OSError:
            pass