"""Construccion y analisis de mensajes SIP/2.0 (RFC 3261)."""

from __future__ import annotations

import random
import string

CRLF = b"\r\n"
SIP_VERSION = "SIP/2.0"
_TOKEN_ALPHABET = string.ascii_letters + string.digits

DEFAULT_HEADERS: list[tuple[str, str]] = []


class SipParseError(ValueError):
    pass


def random_token(length: int = 10) -> str:
    return "".join(random.choice(_TOKEN_ALPHABET) for _ in range(length))


class SipMessage:
    """Mensaje SIP con headers ordenados y duplicados preservados."""

    def __init__(
        self,
        is_request: bool,
        method: str | None = None,
        status: int | None = None,
        reason: str = "",
        headers: list[tuple[str, str]] | None = None,
        body: bytes = b"",
        request_uri: str = "",
    ) -> None:
        self.is_request = is_request
        self.method = method
        self.status = status
        self.reason = reason
        self.headers: list[tuple[str, str]] = headers or []
        self.body = body
        self.request_uri = request_uri

    def header(self, name: str, default: str | None = None) -> str | None:
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return default

    def headers_all(self, name: str) -> list[str]:
        wanted = name.lower()
        return [value for key, value in self.headers if key.lower() == wanted]

    def add_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def encode(self) -> bytes:
        if self.is_request:
            start_line = f"{self.method} {self.start_target} {SIP_VERSION}"
        else:
            start_line = f"{SIP_VERSION} {self.status} {self.reason}".rstrip()

        lines = [start_line]
        for name, value in self.headers:
            lines.append(f"{name}: {value}")
        lines.append("")
        lines.append("")

        head = CRLF.join(line.encode("utf-8") for line in lines)
        return head + self.body

    @property
    def start_target(self) -> str:
        if self.request_uri:
            return self.request_uri
        for key, value in self.headers:
            if key.lower() == "request-uri":
                return value
        raise SipParseError("mensaje sin Request-URI")

    def __repr__(self) -> str:
        if self.is_request:
            head = f"<{self.method} {self.start_target}>"
        else:
            head = f"<{self.status} {self.reason}>"
        return f"SipMessage({head}, headers={len(self.headers)})"


def parse_message(data: bytes) -> SipMessage:
    """Analiza un datagrama SIP. Acepta separadores CRLF y LF."""
    separator = data.find(b"\r\n\r\n")
    sep_len = 4
    if separator == -1:
        separator = data.find(b"\n\n")
        sep_len = 2
    if separator == -1:
        raise SipParseError("mensaje sin linea en blanco entre headers y cuerpo")

    head = data[:separator].decode("utf-8", errors="replace")
    body = data[separator + sep_len :]

    raw_lines = head.replace("\r\n", "\n").split("\n")
    unfolded: list[str] = []
    for line in raw_lines:
        if line[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += " " + line.strip()
        else:
            unfolded.append(line)

    if not unfolded or not unfolded[0].strip():
        raise SipParseError("mensaje sin linea inicial")

    start_line = unfolded[0].strip()
    headers: list[tuple[str, str]] = []
    for line in unfolded[1:]:
        if not line.strip():
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise SipParseError(f"header sin dos puntos: {line!r}")
        headers.append((name.strip(), value.strip()))

    if start_line.upper().startswith("SIP/"):
        parts = start_line.split(None, 2)
        if len(parts) < 2:
            raise SipParseError(f"linea de estado invalida: {start_line!r}")
        try:
            status = int(parts[1])
        except ValueError as exc:
            raise SipParseError(f"codigo de estado invalido: {parts[1]!r}") from exc
        return SipMessage(False, status=status, reason=parts[2] if len(parts) > 2 else "", headers=headers, body=body)

    parts = start_line.split()
    if len(parts) != 3:
        raise SipParseError(f"linea de peticion invalida: {start_line!r}")
    method, uri, version = parts
    if version.upper() != SIP_VERSION:
        raise SipParseError(f"version SIP no soportada: {version!r}")
    return SipMessage(True, method=method.upper(), headers=headers, body=body, request_uri=uri)


def parse_uri(value: str) -> dict[str, str]:
    """Extrae usuario, host, puerto y parametros de un URI SIP."""
    uri = value.strip()
    if uri.startswith("<") and uri.endswith(">"):
        uri = uri[1:-1]
    scheme, _, remainder = uri.partition(":")
    if scheme.lower() not in ("sip", "sips"):
        raise ValueError(f"URI no SIP: {value!r}")

    header_params = ""
    if "?" in remainder:
        remainder, _, header_params = remainder.partition("?")

    if "@" in remainder:
        user, _, host_part = remainder.rpartition("@")
    else:
        user, host_part = "", remainder

    host_port, _, uri_params = host_part.partition(";")

    host = host_port
    port = ""
    if host_port.startswith("["):
        closing = host_port.find("]")
        if closing != -1:
            host = host_port[1:closing]
            if host_port[closing + 1 :].startswith(":"):
                port = host_port[closing + 2 :]
    elif ":" in host_port:
        host, _, port = host_port.partition(":")

    params = ";".join(part for part in (uri_params, header_params) if part)

    return {
        "scheme": scheme.lower(),
        "user": user,
        "host": host,
        "port": port,
        "params": params,
    }


def parse_messages_waiting(value: str | None) -> tuple[bool, int] | None:
    """Interpreta el header Messages-Waiting (indicador de buzon de voz).

    Devuelve (mensajes_pendientes, cantidad) o None si el header no existe.
    Ejemplos: 'yes;voicemail=3' -> (True, 3), 'no' -> (False, 0).
    """
    if value is None:
        return None
    parts = [chunk.strip() for chunk in value.split(";")]
    flag = parts[0].strip().lower()
    if flag not in ("yes", "no"):
        return None

    count = 0
    for chunk in parts[1:]:
        name, _, raw = chunk.partition("=")
        if name.strip().lower() == "voicemail":
            try:
                count = int(raw.strip())
            except ValueError:
                count = 0
    return flag == "yes", max(0, count)


def contact_expires(contact_value: str) -> int | None:
    """Lee el parametro expires= de un header Contact."""
    value = contact_value.strip()
    if value.startswith("<") and ">" in value:
        value = value[value.find(">") + 1 :]
    for chunk in value.split(";")[1:]:
        name, _, param_value = chunk.partition("=")
        if name.strip().lower() == "expires":
            try:
                return int(param_value.strip().strip('"'))
            except ValueError:
                return None
    return None
