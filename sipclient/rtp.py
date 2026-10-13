"""RTP/AVP para voz G.711 (RFC 3550): endpoint UDP y empaquetado."""

from __future__ import annotations

import random
import socket
import struct
from dataclasses import dataclass

RTP_HDR = struct.Struct(">BBHII")
FRAME_SAMPLES = 160  # 20 ms a 8000 Hz
SAMPLE_RATE = 8000


@dataclass
class RtpFrame:
    payload_type: int
    sequence: int
    timestamp: int
    payload: bytes


class RtpError(OSError):
    pass


class RtpSocket:
    """Socket RTP que envia y recibe payloads de voz."""

    def __init__(self, local_ip: str = "0.0.0.0", local_port: int = 0) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._socket.bind((local_ip, local_port))
        except OSError as exc:
            self._socket.close()
            raise RtpError(f"No se pudo abrir el puerto RTP: {exc}") from exc
        self._socket.settimeout(0.005)
        self._remote: tuple[str, int] | None = None
        self._sequence = random.randint(0, 65535)
        self._timestamp = random.randint(0, 0xFFFFFFFF)
        self._ssrc = random.randint(1, 0xFFFFFFFE)
        self._send_count = 0

    @property
    def local_ip(self) -> str:
        return str(self._socket.getsockname()[0])

    @property
    def local_port(self) -> int:
        return int(self._socket.getsockname()[1])

    def set_remote(self, host: str, port: int) -> None:
        self._remote = (host, port)

    def send_payload(self, payload: bytes, payload_type: int, marker: bool = False,
                     ts_advance: int = FRAME_SAMPLES) -> None:
        if self._remote is None:
            raise RtpError("Sin destino RTP configurado.")
        marker_bit = 0x80 if marker else 0
        header = RTP_HDR.pack(
            0x80,
            marker_bit | (payload_type & 0x7F),
            self._sequence,
            self._timestamp & 0xFFFFFFFF,
            self._ssrc,
        )
        self._socket.sendto(header + payload, self._remote)
        self._sequence = (self._sequence + 1) & 0xFFFF
        self._timestamp = (self._timestamp + ts_advance) & 0xFFFFFFFF
        self._send_count += 1

    _DTMF_EVENTS = {"*": 10, "#": 11, "A": 12, "B": 13, "C": 14, "D": 15}

    def send_dtmf_event(self, digit: str, payload_type: int = 101, duration_ms: int = 80) -> None:
        """Envia un evento DTMF telefonico (RFC 4733: paquete inicio + fin)."""
        if self._remote is None:
            raise RtpError("Sin destino RTP configurado.")
        if digit.isdigit():
            event = int(digit)
        elif digit in self._DTMF_EVENTS:
            event = self._DTMF_EVENTS[digit]
        else:
            raise ValueError(f"DTMF no valido: {digit!r}")
        volume = 10
        advance = int(round(duration_ms * SAMPLE_RATE / 1000))
        start = bytes([event, 0x80 | volume, 0x00, 0x00])  # R=1, E=0
        end = bytes([event, 0x40 | volume, 0x00, duration_ms])  # E=1, R=0
        self.send_payload(start, payload_type, ts_advance=advance // 2)
        self.send_payload(end, payload_type, ts_advance=advance - advance // 2)

    def drain(self) -> list[RtpFrame]:
        """Recoge sin bloquear los paquetes RTP recibidos."""
        frames: list[RtpFrame] = []
        while True:
            try:
                data, _peer = self._socket.recvfrom(2048)
            except socket.timeout:
                return frames
            except OSError:
                return frames
            if len(data) < 12:
                continue
            version = data[0] >> 6
            if version != 2:
                continue
            _ext = (data[0] >> 4) & 0x01
            csrc_count = data[0] & 0x0F
            payload_type, sequence, timestamp = RTP_HDR.unpack_from(data)[1:4]
            offset = 12 + csrc_count * 4
            frames.append(RtpFrame(payload_type, sequence, timestamp, data[offset:]))
        return frames  # pragma: no cover

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass

    def __enter__(self) -> "RtpSocket":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()