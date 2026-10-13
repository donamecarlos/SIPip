"""Persistencia de la contrasena de la extension.

En Windows se guarda como credencial generica protegida por DPAPI en el
Administrador de credenciales. En el resto de plataformas (Android) se guarda
en el directorio privado de la aplicacion con una ofuscacion ligera: alli el
directorio de datos de la app ya es privado por usuario y por aplicacion.
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .config import config_dir

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
_CREDENTIAL_PREFIX = "SIPip"
_OBFUSCATION_KEY = b"SIPip-file-v1"

CREDENTIAL_BACKEND = "windows" if os.name == "nt" else "file"


class CredentialError(RuntimeError):
    pass


class _CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CREDENTIAL_ATTRIBUTEW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _advapi32() -> ctypes.WinDLL:
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    library.CredReadW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    library.CredReadW.restype = wintypes.BOOL
    library.CredDeleteW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    return library


def _target_name(server_host: str, extension: str) -> str:
    return f"{_CREDENTIAL_PREFIX}/{server_host.lower()}/{extension}"


def _cred_store_password(server_host: str, extension: str, password: str) -> None:
    library = _advapi32()
    blob = password.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob, len(blob))
    target = _target_name(server_host, extension)

    credential = _CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = extension
    credential.Comment = "SIP extension password"

    if not library.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError(f"CredWriteW fallo (error {ctypes.get_last_error()})")


def _cred_load_password(server_host: str, extension: str) -> str | None:
    library = _advapi32()
    pointer = ctypes.POINTER(_CREDENTIALW)()
    target = _target_name(server_host, extension)

    if not library.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise CredentialError(f"CredReadW fallo (error {error})")

    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return blob.decode("utf-16-le")
    finally:
        library.CredFree(pointer)


def _cred_delete_password(server_host: str, extension: str) -> None:
    library = _advapi32()
    if not library.CredDeleteW(_target_name(server_host, extension), CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise CredentialError(f"CredDeleteW fallo (error {error})")


def _file_password_path(server_host: str, extension: str) -> Path:
    safe_server = "".join(ch for ch in server_host.lower() if ch.isalnum() or ch in "._-")
    return config_dir() / "credentials" / f"{safe_server}_{extension}.dat"


def _file_store_password(server_host: str, extension: str, password: str) -> None:
    path = _file_password_path(server_host, extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = password.encode("utf-8")
    obscured = bytes(
        byte ^ _OBFUSCATION_KEY[index % len(_OBFUSCATION_KEY)] for index, byte in enumerate(raw)
    )
    path.write_bytes(base64.b64encode(obscured))


def _file_load_password(server_host: str, extension: str) -> str | None:
    path = _file_password_path(server_host, extension)
    if not path.is_file():
        return None
    try:
        obscured = base64.b64decode(path.read_bytes())
    except (OSError, ValueError):
        return None
    raw = bytes(
        byte ^ _OBFUSCATION_KEY[index % len(_OBFUSCATION_KEY)]
        for index, byte in enumerate(obscured)
    )
    return raw.decode("utf-8", errors="replace")


def _file_delete_password(server_host: str, extension: str) -> None:
    path = _file_password_path(server_host, extension)
    if path.is_file():
        path.unlink()


def store_password(server_host: str, extension: str, password: str) -> None:
    if CREDENTIAL_BACKEND == "windows":
        _cred_store_password(server_host, extension, password)
    else:
        _file_store_password(server_host, extension, password)


def load_password(server_host: str, extension: str) -> str | None:
    if CREDENTIAL_BACKEND == "windows":
        return _cred_load_password(server_host, extension)
    return _file_load_password(server_host, extension)


def delete_password(server_host: str, extension: str) -> None:
    if CREDENTIAL_BACKEND == "windows":
        _cred_delete_password(server_host, extension)
    else:
        _file_delete_password(server_host, extension)
