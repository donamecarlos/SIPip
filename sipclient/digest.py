"""Digest authentication para SIP (RFC 3261 seccion 22.4 / RFC 2617)."""

from __future__ import annotations

import hashlib
import random
import string

CNONCE_ALPHABET = string.ascii_lowercase + string.digits


def md5_hex(text: str) -> str:
    """MD5 en hexadecimal. usedforsecurity=False permite FIPS en Windows."""
    data = text.encode("utf-8")
    try:
        return hashlib.md5(data, usedforsecurity=False).hexdigest()
    except TypeError:
        return hashlib.md5(data).hexdigest()


def new_cnonce(length: int = 16) -> str:
    return "".join(random.choice(CNONCE_ALPHABET) for _ in range(length))


def parse_auth_header(value: str) -> dict[str, str]:
    """Convierte un header WWW-Authenticate/Proxy-Authenticate en dict de params."""
    scheme, _, rest = value.strip().partition(" ")
    if scheme.lower() != "digest":
        raise ValueError(f"esquema de autenticacion no soportado: {scheme!r}")

    params: dict[str, str] = {}
    index = 0
    length = len(rest)

    def skip_separators(pos: int) -> int:
        while pos < length and rest[pos] in ", \t":
            pos += 1
        return pos

    def skip_to_next_param(pos: int) -> int:
        while pos < length and rest[pos] != ",":
            pos += 1
        return pos

    while True:
        index = skip_separators(index)
        if index >= length:
            break

        start = index
        while index < length and rest[index] != "=":
            index += 1
        name = rest[start:index].strip().lower()
        index += 1

        if index < length and rest[index] == '"':
            index += 1
            chunks: list[str] = []
            while index < length and rest[index] != '"':
                if rest[index] == "\\" and index + 1 < length:
                    index += 1
                chunks.append(rest[index])
                index += 1
            index += 1
            parsed = "".join(chunks)
        else:
            start = index
            index = skip_to_next_param(index)
            parsed = rest[start:index].strip()

        if name:
            params[name] = parsed
        index = skip_to_next_param(index)

    return params


def compute_response(
    username: str,
    password: str,
    realm: str,
    nonce: str,
    method: str,
    uri: str,
    qop: str | None = None,
    cnonce: str | None = None,
    nc: str | None = None,
    algorithm: str = "MD5",
) -> str:
    """Calcula el campo 'response' del header Digest."""
    if algorithm.lower() in ("", "md5"):
        ha1 = md5_hex(f"{username}:{realm}:{password}")
    elif algorithm.lower() == "md5-sess":
        if not cnonce:
            raise ValueError("MD5-sess requiere cnonce")
        ha1 = md5_hex(f"{md5_hex(f'{username}:{realm}:{password}')}:{nonce}:{cnonce}")
    else:
        raise ValueError(f"algoritmo no soportado: {algorithm}")

    ha2 = md5_hex(f"{method}:{uri}")

    if qop:
        if not cnonce or not nc:
            raise ValueError("qop requiere cnonce y nc")
        return md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return md5_hex(f"{ha1}:{nonce}:{ha2}")


class DigestAuthenticator:
    """Mantiene el estado del reto/respuesta digest para una sesion."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self._password = password
        self.challenge: dict[str, str] | None = None
        self.cnonce = new_cnonce()
        self.nc = 0

    @property
    def challenged(self) -> bool:
        return self.challenge is not None

    def challenge_with(self, header_value: str) -> None:
        params = parse_auth_header(header_value)
        if "realm" not in params:
            raise ValueError("el reto digest no incluye realm")
        self.challenge = params
        self.nc = 0

    def build_header(self, method: str, uri: str, nonce_count: str | None = None) -> str:
        if self.challenge is None:
            raise RuntimeError("no hay reto digest registrado")

        params = self.challenge
        realm = params["realm"]
        nonce = params["nonce"]
        algorithm = params.get("algorithm", "MD5")
        qop = params.get("qop")

        chosen_qop = None
        if qop:
            options = [item.strip() for item in qop.split(",") if item.strip()]
            chosen_qop = "auth" if "auth" in options else (options[0] if options else None)

        if nonce_count is not None:
            nc = nonce_count
            self.nc = int(nc, 16)
        else:
            self.nc += 1
            nc = f"{self.nc:08x}"

        response = compute_response(
            username=self.username,
            password=self._password,
            realm=realm,
            nonce=nonce,
            method=method,
            uri=uri,
            qop=chosen_qop,
            cnonce=self.cnonce if chosen_qop else None,
            nc=nc if chosen_qop else None,
            algorithm=algorithm,
        )

        fields = [
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'username="{self.username}"',
            f'uri="{uri}"',
            f'response="{response}"',
            f"algorithm={algorithm}",
        ]
        if chosen_qop:
            fields.append(f"qop={chosen_qop}")
            fields.append(f"nc={nc}")
            fields.append(f'cnonce="{self.cnonce}"')
        if params.get("opaque"):
            fields.append(f'opaque="{params["opaque"]}"')

        return "Digest " + ", ".join(fields)
