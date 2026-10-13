"""Oferta/respuesta SDP para sesiones RTP (RFC 4566)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from . import __version__

SUPPORTED_CODECS: tuple[tuple[str, int, int], ...] = (
    ("PCMU", 0, 8000),
    ("PCMA", 8, 8000),
    ("telephone-event", 101, 8000),
)

_PREFERRED = ("PCMU", "PCMA")


@dataclass
class SdpMedia:
    codec: str
    payload_type: int
    clock_rate: int
    connection_ip: str
    port: int


class SdpError(ValueError):
    pass


def build_offer(local_ip: str, rtp_port: int, codecs: Iterable[tuple[str, int, int]] | None = None) -> str:
    """Construye una oferta SDP estandar para una llamada telefonica."""
    chosen = list(codecs) if codecs is not None else list(SUPPORTED_CODECS)
    payloads = [str(payload_type) for _name, payload_type, _rate in chosen]
    lines = [
        "v=0",
        f"o=SIPip {0} {0} IN IP4 {local_ip}",
        f"s={__version__} call",
        f"c=IN IP4 {local_ip}",
        "t=0 0",
        f"m=audio {rtp_port} RTP/AVP {' '.join(payloads)}",
        "a=sendrecv",
    ]
    for name, payload_type, rate in chosen:
        lines.append(f"a=rtpmap:{payload_type} {name}/{rate}")
        if name == "telephone-event":
            lines.append(f"a=fmtp:{payload_type} 0-16")
    return "\r\n".join(lines) + "\r\n"


def _media_lines(body: str) -> list[int]:
    return [
        index
        for index, line in enumerate(body.splitlines())
        if line.startswith("m=")
    ]


def _global_connection(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("c="):
            parts = line.split()
            if len(parts) >= 3:
                return parts[2]
    return ""


def _fmtp_events(body: str, payload_type: int) -> str:
    for line in body.splitlines():
        if line.startswith(f"a=fmtp:{payload_type} "):
            return line.split(" ", 1)[1]
    return ""


def parse_answer(body: str) -> SdpMedia:
    """Extrae el flujo de audio y el códec de la respuesta remota.

    Se elige el primer códec soportado (PCMU > PCMA); el campo telephone-event
    se conserva aparte en ``SdpMedia`` solo si es el flujo elegido.
    """
    if not body or not body.strip():
        raise SdpError("La respuesta no incluye cuerpo SDP.")

    media = _media_lines(body)
    if not media:
        raise SdpError("La respuesta SDP no contiene flujo de medios.")
    audio_index = media[0]
    lines = body.splitlines()

    fields = lines[audio_index].split()
    if len(fields) < 4 or fields[0].lower() != "m=audio":
        raise SdpError("El primer flujo SDP no es audio.")

    try:
        remote_port = int(fields[1])  # m=audio <port> RTP/AVP ...
    except ValueError as exc:
        raise SdpError(f"Puerto SDP invalido: {fields[1]!r}") from exc

    offered = [int(token) for token in fields[3:] if token.isdigit()]
    if not offered:
        raise SdpError("El flujo de audio no anuncia ningun payload.")

    connection_ip = _global_connection(body)
    rtpmaps: dict[int, tuple[str, int]] = {}
    for line in lines[audio_index:]:
        match = re.match(r"a=rtpmap:(\d+)\s+(\w[\w-]*)/(\d+)", line)
        if match:
            rtpmaps[int(match.group(1))] = (match.group(2).upper(), int(match.group(3)))
        if line.startswith("c=IN IP4"):
            parts = line.split()
            if len(parts) >= 3:
                connection_ip = parts[2]

    if not connection_ip:
        raise SdpError("La respuesta SDP no incluye direccion IP del media.")

    chosen_codec = ""
    chosen_type = -1
    choice_rate = 8000
    for offered_type in offered:
        if offered_type in rtpmaps:
            name, rate = rtpmaps[offered_type]
            if name in _PREFERRED and (chosen_codec == "" or _PREFERRED.index(name) < _PREFERRED.index(chosen_codec)):
                chosen_codec = name
                chosen_type = offered_type
                choice_rate = rate
    if not chosen_codec:
        names = [f"{name}/{rate}" for _pt, (name, rate) in sorted(rtpmaps.items())]
        if names:
            raise SdpError("El remoto ofrece solo codecs no soportados: " + ", ".join(names))
        chosen_codec = "PCMU"
        chosen_type = offered[0]
        choice_rate = 8000

    return SdpMedia(
        codec=chosen_codec,
        payload_type=chosen_type,
        clock_rate=choice_rate,
        connection_ip=connection_ip,
        port=remote_port,
    )