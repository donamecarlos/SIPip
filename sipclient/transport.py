"""Transportes UDP y TCP para la senalizacion SIP del cliente.

Ambas familias exponen la misma API de socket conectado (send/recv/local) y la
misma API de listener (recvfrom/sendto), de modo que el registro y el motor de
llamadas funcionan igual con UDP o con TCP.  La voz (RTP) sigue siendo UDP.
"""

from __future__ import annotations

import queue
import socket
import threading
import time

from .trace import write as _trace_write

SipTransport = "UdpTransport | TcpTransport"
SipListener = "UdpListener | TcpListener"


def _trace(direction: str, peer: str, data: bytes) -> None:
    _trace_write(direction, peer, data)


MAX_SIP_MESSAGE_LENGTH = 64 * 1024


def _frame_boundaries(buffer: bytes) -> tuple[int | None, int]:
    """Devuelve (fin_del_bloque_de_cabeceras, content_length) de un SIP en TCP."""
    for separator in (b"\r\n\r\n", b"\n\n"):
        end = buffer.find(separator)
        if end == -1:
            continue
        content_length = 0
        try:
            head = buffer[:end].decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - una cabecera ilegible nunca rompe el framing
            head = ""
        for line in head.splitlines():
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-length":
                try:
                    content_length = max(0, int(value.strip()))
                except ValueError:
                    content_length = 0
                break
        return end + len(separator), content_length
    return None, 0


def recv_sip_from_stream(sock: socket.socket, deadline: float) -> bytes | None:
    """Lee un mensaje SIP completo de un socket TCP.

    Devuelve los bytes del mensaje, None si se agota el plazo sin llegar a un
    mensaje completo, o bytes vacios si la conexion remota se cerro.
    """
    data = bytearray()
    while True:
        boundary, content_length = _frame_boundaries(bytes(data))
        if boundary is not None and len(data) >= boundary + content_length:
            return bytes(data[: boundary + content_length])
        if len(data) > MAX_SIP_MESSAGE_LENGTH:
            return b""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        sock.settimeout(min(remaining, 0.5))
        try:
            chunk = sock.recv(65535)
        except socket.timeout:
            continue
        except OSError:
            return b""
        if not chunk:
            return b""
        data += chunk


def new_transport(transport: str, server_host: str, server_port: int):
    """Crea el socket conectado adecuado segun el transporte configurado."""
    if transport.lower() == "tcp":
        return TcpTransport(server_host, server_port)
    return UdpTransport(server_host, server_port)


def new_listener(transport: str, local_port: int = 0):
    """Crea el punto de escucha adecuado segun el transporte configurado."""
    if transport.lower() == "tcp":
        return TcpListener(local_port=local_port)
    return UdpListener(local_port=local_port)


class UdpTransport:
    """Socket UDP conectado al servidor SIP.

    Conectar el socket UDP permite descartar datagramas de otros emisores y
    obtener la IP/puerto local real, necesario para el header Contact.
    """

    def __init__(
        self,
        server_host: str,
        server_port: int,
        bind_address: str = "0.0.0.0",
        local_port: int = 0,
    ) -> None:
        self.server = (server_host, server_port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((bind_address, local_port))
        try:
            self._socket.connect(self.server)
        except OSError:
            self._socket.close()
            raise
        self._closed = False

    @property
    def local_address(self) -> tuple[str, int]:
        address, port = self._socket.getsockname()[:2]
        return str(address), int(port)

    @property
    def local_ip(self) -> str:
        return self.local_address[0]

    @property
    def local_port(self) -> int:
        return self.local_address[1]

    def send(self, data: bytes) -> None:
        _trace(">> enviado", f"{self.server[0]}:{self.server[1]}", data)
        self._socket.send(data)

    def recv(self, deadline: float | None = None) -> bytes | None:
        """Recibe un datagrama. Devuelve None si se agota el plazo."""
        if self._closed:
            return None
        if deadline is None:
            timeout: float | None = None
        else:
            timeout = max(0.0, deadline - time.monotonic())
        if timeout == 0.0:
            return None

        self._socket.settimeout(timeout)
        try:
            data, _ = self._socket.recvfrom(65535)
        except socket.timeout:
            return None
        except ConnectionResetError:
            return None
        except OSError:
            return None
        _trace("<< recibido", f"{self.server[0]}:{self.server[1]}", data)
        return data

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._socket.close()
            except OSError:
                pass

    def __enter__(self) -> "UdpTransport":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


class UdpListener:
    """Socket UDP sin conectar para recibir llamadas entrantes.

    Se usa como contacto estable de la extension: los INVITE del servidor llegan
    aqui en cualquier momento, por eso no se conecta a un destino fijo.
    """

    def __init__(
        self,
        bind_address: str = "0.0.0.0",
        local_port: int = 0,
    ) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._socket.bind((bind_address, local_port))
        except OSError:
            self._socket.close()
            raise
        self._closed = False

    @property
    def local_address(self) -> tuple[str, int]:
        address, port = self._socket.getsockname()[:2]
        return str(address), int(port)

    @property
    def local_ip(self) -> str:
        return self.local_address[0]

    @property
    def local_port(self) -> int:
        return self.local_address[1]

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        _trace(">> enviado", f"{address[0]}:{address[1]}", data)
        self._socket.sendto(data, address)

    def recvfrom(self, deadline: float) -> tuple[bytes, tuple[str, int]] | None:
        """Recibe un datagrama con su origen. Devuelve None si se agota el plazo."""
        if self._closed:
            return None
        timeout = max(0.0, deadline - time.monotonic())
        if timeout == 0.0:
            return None
        self._socket.settimeout(timeout)
        try:
            data, address = self._socket.recvfrom(65535)
        except socket.timeout:
            return None
        except OSError:
            return None
        peer = (str(address[0]), int(address[1]))
        _trace("<< recibido", f"{peer[0]}:{peer[1]}", data)
        return data, peer

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._socket.close()
            except OSError:
                pass

    def __enter__(self) -> "UdpListener":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


class TcpTransport:
    """Socket TCP conectado al servidor SIP (senalizacion por streaming).

    Los mensajes se delimitan por el header Content-Length, por eso recv()
    devuelve siempre un mensaje completo aunque llegue fragmentado en varios
    segmentos TCP.
    """

    def __init__(
        self,
        server_host: str,
        server_port: int,
        bind_address: str = "0.0.0.0",
        local_port: int = 0,
        connect_timeout: float = 5.0,
    ) -> None:
        self.server = (server_host, server_port)
        self._buffer = bytearray()
        self._closed = False
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((bind_address, local_port))
        self._socket.settimeout(connect_timeout)
        try:
            self._socket.connect(self.server)
        except OSError:
            self._socket.close()
            raise
        self._socket.settimeout(None)

    @property
    def local_address(self) -> tuple[str, int]:
        address, port = self._socket.getsockname()[:2]
        return str(address), int(port)

    @property
    def local_ip(self) -> str:
        return self.local_address[0]

    @property
    def local_port(self) -> int:
        return self.local_address[1]

    def send(self, data: bytes) -> None:
        _trace(">> enviado", f"{self.server[0]}:{self.server[1]}", data)
        self._socket.sendall(data)

    def recv(self, deadline: float | None = None) -> bytes | None:
        """Recibe el siguiente mensaje SIP completo. None si se agota el plazo."""
        if self._closed:
            return None
        own_deadline = time.monotonic() + 3600 if deadline is None else deadline
        while not self._closed:
            message = self._extract_message()
            if message is not None:
                _trace("<< recibido", f"{self.server[0]}:{self.server[1]}", message)
                return message
            remaining = own_deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._socket.settimeout(min(remaining, 0.5))
            try:
                chunk = self._socket.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                self._close_socket()
                return None
            if not chunk:
                self._close_socket()
                return None
            self._buffer += chunk
            if len(self._buffer) > MAX_SIP_MESSAGE_LENGTH:
                self._close_socket()
                return None
        return None

    def _extract_message(self) -> bytes | None:
        if not self._buffer:
            return None
        boundary, content_length = _frame_boundaries(bytes(self._buffer))
        if boundary is None:
            return None
        if len(self._buffer) < boundary + content_length:
            return None
        message = bytes(self._buffer[: boundary + content_length])
        del self._buffer[: boundary + content_length]
        return message

    def _close_socket(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._socket.close()
            except OSError:
                pass

    def close(self) -> None:
        self._close_socket()

    def __enter__(self) -> "TcpTransport":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


class TcpListener:
    """Listener TCP para llamadas entrantes.

    Acepta conexiones y reexpone la API de UdpListener (recvfrom/sendto): cada
    mensaje llega etiquetado con la direccion remota y las respuestas se
    envian por la conexion correcta, como exige el SIP sobre TCP.
    """

    def __init__(
        self,
        bind_address: str = "0.0.0.0",
        local_port: int = 0,
    ) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._socket.bind((bind_address, local_port))
            self._socket.listen(64)
        except OSError:
            self._socket.close()
            raise
        self._closed = False
        self._running = True
        self._lock = threading.Lock()
        self._connections: dict[tuple[str, int], socket.socket] = {}
        self._queue: queue.Queue[tuple[bytes, tuple[str, int]]] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="tcp-accept", daemon=True
        )
        self._accept_thread.start()

    @property
    def local_address(self) -> tuple[str, int]:
        address, port = self._socket.getsockname()[:2]
        return str(address), int(port)

    @property
    def local_ip(self) -> str:
        return self.local_address[0]

    @property
    def local_port(self) -> int:
        return self.local_address[1]

    def _accept_loop(self) -> None:
        while self._running:
            self._socket.settimeout(0.5)
            try:
                conn, addr = self._socket.accept()
            except (socket.timeout, OSError):
                if not self._running:
                    break
                continue
            peer = (str(addr[0]), int(addr[1]))
            conn.settimeout(None)
            with self._lock:
                self._connections[peer] = conn
            reader = threading.Thread(
                target=self._read_loop, args=(conn, peer), name="tcp-read", daemon=True
            )
            with self._lock:
                self._threads.append(reader)
            reader.start()

    def _read_loop(self, conn: socket.socket, peer: tuple[str, int]) -> None:
        buffer = bytearray()
        while self._running and not self._closed:
            boundary, content_length = _frame_boundaries(bytes(buffer))
            if boundary is not None and len(buffer) >= boundary + content_length:
                message = bytes(buffer[: boundary + content_length])
                del buffer[: boundary + content_length]
                _trace("<< recibido", f"{peer[0]}:{peer[1]}", message)
                self._queue.put((message, peer))
                continue
            if len(buffer) > MAX_SIP_MESSAGE_LENGTH:
                break
            conn.settimeout(0.5)
            try:
                chunk = conn.recv(65535)
            except (socket.timeout, OSError):
                continue
            if not chunk:
                break
            buffer += chunk
        self._drop(peer, conn)

    def _drop(self, peer: tuple[str, int], conn: socket.socket | None = None) -> None:
        with self._lock:
            existing = self._connections.pop(peer, None)
        if existing is None and conn is not None:
            existing = conn
        if existing is not None:
            try:
                existing.close()
            except OSError:
                pass

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        peer = (str(address[0]), int(address[1]))
        _trace(">> enviado", f"{peer[0]}:{peer[1]}", data)
        with self._lock:
            conn = self._connections.get(peer)
        if conn is None:
            raise OSError("No hay conexion TCP con el destino.")
        try:
            conn.sendall(data)
        except OSError:
            self._drop(peer, conn)
            raise

    def recvfrom(self, deadline: float) -> tuple[bytes, tuple[str, int]] | None:
        """Devuelve el siguiente mensaje con su origen. None si se agota el plazo."""
        if self._closed:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            item = self._queue.get(timeout=remaining)
        except queue.Empty:
            return None
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._running = False
        try:
            self._socket.close()
        except OSError:
            pass
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            threads = list(self._threads)
            self._threads.clear()
        for conn in connections:
            try:
                conn.close()
            except OSError:
                pass
        for thread in threads:
            thread.join(timeout=1.0)

    def __enter__(self) -> "TcpListener":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @property
    def connections(self) -> int:
        with self._lock:
            return len(self._connections)
