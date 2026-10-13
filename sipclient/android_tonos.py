"""Reproduccion de los tonos del softphone en Android.

Si existe el MP3 empaquetado en `tonos/` se reproduce con Kivy SoundLoader
(SDL2). Si no, se genera un tono de llamada sencillo (pitido de 425 Hz en
32 x 320 ms = ciclo de llamada tipico) a traves de AudioTrack, de modo que la
app siempre pueda avisar de llamadas entrantes sin depender de archivos.
"""

from __future__ import annotations

import math
import struct
import threading
import time
from pathlib import Path

from .tonos import TONES, TonosError, tonos_dir


class AndroidToneError(OSError):
    pass


def _pcm_tone(seconds: float, frequency: float, sample_rate: int = 8000) -> bytes:
    total = int(seconds * sample_rate)
    if total <= 0:
        return b""
    amplitude = 8000  # suficiente sin saturar el codec G.711 a 8 kHz
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate)))
        for i in range(total)
    )


_CADENCE = (0.32, 0.48)  # 320 ms tono, 480 ms silencio (bucle clasico de llamada)


class _BeepRing:
    """Bucle de pitido generado via AudioTrack mientras la llamada suena."""

    def __init__(self) -> None:
        self._stop_request = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_request.clear()
        self._thread = threading.Thread(target=self._loop, name="sipip-ringtone", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        from .android_audio import _AndroidPlayback

        on = _pcm_tone(_CADENCE[0], 425.0)
        off = b"\x00\x00" * (int(_CADENCE[1] * 8000) * 2)
        playback = _AndroidPlayback()
        try:
            playback.open()
        except Exception:  # noqa: BLE001 - sin tono la llamada sigue
            return
        try:
            while not self._stop_request.is_set():
                playback.play(on)
                if self._stop_request.wait(_CADENCE[1]):
                    break
                playback.play(off) if off else None
        finally:
            playback.close()

    def stop(self) -> None:
        self._stop_request.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)


class AndroidTones:
    """Reproduce un tono a la vez; cada nuevo reproduce corta el anterior."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory is not None else tonos_dir()
        self.played: list[str] = []
        self._sound = None
        self._beep = _BeepRing()

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
        except TonosError:
            return False
        self.stop()
        if path.is_file():
            from kivy.core.audio import SoundLoader

            sound = SoundLoader.load(str(path))
            if sound is None:
                self.played.append(name)
                return False
            sound.loop = loop
            sound.volume = 1.0
            sound.play()
            self._sound = sound
            self._beep = _BeepRing()
            self.played.append(name)
            return True
        if loop and name == "llamada_entrante":
            self._beep.start()
            self.played.append(name)
            return True
        return False

    def stop(self) -> None:
        sound = self._sound
        self._sound = None
        if sound is not None:
            sound.stop()
            sound.unload()
        self._beep.stop()
        self._beep = _BeepRing()

    def close(self) -> None:
        self.stop()