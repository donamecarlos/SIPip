"""Reproduccion de los tonos del softphone (carpeta tonos/) via MCI.

Los archivos son MP3 proporcionados por el usuario en la carpeta `tonos/` del
proyecto. En Windows se reproducen con el dispositivo MCI mpegvideo para no
depender de librerias de decodificacion.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

TONES: dict[str, str] = {
    "llamada_entrante": "llamada entrante.mp3",
    "fuera_de_servicio": "fuera de servicio.mp3",
    "telefono_colgado": "telefono colgado.mp3",
}

_DEFAULT_TONOS_DIR = Path(__file__).resolve().parent.parent / "tonos"


class TonosError(OSError):
    pass


def tonos_dir() -> Path:
    override = os.environ.get("SIPIP_TONOS_DIR")
    if override:
        return Path(override)
    return _DEFAULT_TONOS_DIR


try:
    _winmm = ctypes.WinDLL("winmm")
    _mci_send = _winmm.mciSendStringW
    _mci_send.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    _mci_error = _winmm.mciGetErrorStringW
    _mci_error.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
except (OSError, AttributeError):
    _winmm = None

_MCI_ERR_BUF = 256


def _mci_call(command: str) -> None:
    if _winmm is None:
        raise TonosError("MCI no disponible en esta plataforma.")
    result = _mci_send(command, None, 0, None)
    if result:
        buffer = ctypes.create_unicode_buffer(_MCI_ERR_BUF)
        _mci_error(result, buffer, _MCI_ERR_BUF)
        raise TonosError(f"MCI {command!r}: {buffer.value or result}")


class TonosPlayer:
    """Reproduce un tono a la vez; cada nuevo reproduce corta el anterior."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory is not None else tonos_dir()
        self.played: list[str] = []
        self._alias = "sipip_tono"
        self._opened = False

    def path_for(self, name: str) -> Path:
        candidate = Path(name)
        if candidate.suffix.lower() == ".mp3":
            if candidate.is_absolute():
                return candidate
            return self.directory / candidate
        filename = TONES.get(name)
        if filename is None:
            raise TonosError(f"Tono desconocido: {name}")
        return self.directory / filename

    def available(self, name: str) -> bool:
        return self.path_for(name).is_file()

    def play(self, name: str, loop: bool = False) -> bool:
        try:
            path = self.path_for(name)
            if not path.is_file():
                self.played.append(name)
                return False
            self.stop()
            _mci_call(f'open "{path}" type mpegvideo alias {self._alias}')
            command = f"play {self._alias}"
            if loop:
                command += " repeat"
            _mci_call(command)
            self._opened = True
            self.played.append(name)
            return True
        except TonosError:
            return False

    def stop(self) -> None:
        if not self._opened:
            return
        try:
            _mci_call(f"stop {self._alias}")
            _mci_call(f"close {self._alias}")
        except TonosError:
            pass
        finally:
            self._opened = False

    def close(self) -> None:
        self.stop()


class DummyTones:
    """Sustituto en memoria para pruebas: registra lo que se pide reproducir."""

    def __init__(self) -> None:
        self.played: list[tuple[str, bool]] = []
        self.playing = False

    def play(self, name: str, loop: bool = False) -> bool:
        self.played.append((name, loop))
        self.playing = True
        return True

    def stop(self) -> None:
        self.playing = False

    def close(self) -> None:
        self.playing = False