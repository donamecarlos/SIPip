"""Captura y reproduccion de audio en Windows via winmm (waveIn/waveOut).

No depende de ninguna biblioteca de terceros: se accede a `winmm.dll` con
ctypes. El contenido se normaliza a PCM 16-bit mono a 8000 Hz.
"""

from __future__ import annotations

import array
import ctypes
import queue
import threading
import time
from ctypes import wintypes

CALLBACK_FUNCTION = 0x00030000
WAVE_FORMAT_PCM = 0x0001
WAVE_MAPPER = wintypes.UINT(-1)

WIM_OPEN = 0x3BE
WIM_CLOSE = 0x3BF
WIM_DATA = 0x3C0
WOM_OPEN = 0x3BB
WOM_CLOSE = 0x3BC
WOM_DONE = 0x3BD

MMSYSERR_NOERROR = 0

DEFAULT_RATES = (8000, 16000, 32000, 44100, 48000)
FRAME_SAMPLES = 160


class AudioError(OSError):
    pass


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.POINTER(ctypes.c_char)),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


WAVE_PROC = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)

try:
    _winmm = ctypes.WinDLL("winmm")
    _HWAVE = ctypes.POINTER(ctypes.c_void_p)

    _winmm.waveInOpen.argtypes = [_HWAVE, wintypes.UINT, ctypes.POINTER(WAVEFORMATEX), WAVE_PROC,
                                  ctypes.c_void_p, wintypes.DWORD]
    _winmm.waveInOpen.restype = wintypes.UINT
    _winmm.waveInPrepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveInPrepareHeader.restype = wintypes.UINT
    _winmm.waveInAddBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveInAddBuffer.restype = wintypes.UINT
    _winmm.waveInStart.argtypes = [ctypes.c_void_p]
    _winmm.waveInStop.argtypes = [ctypes.c_void_p]
    _winmm.waveInReset.argtypes = [ctypes.c_void_p]
    _winmm.waveInClose.argtypes = [ctypes.c_void_p]

    _winmm.waveOutOpen.argtypes = [_HWAVE, wintypes.UINT, ctypes.POINTER(WAVEFORMATEX), WAVE_PROC,
                                   ctypes.c_void_p, wintypes.DWORD]
    _winmm.waveOutOpen.restype = wintypes.UINT
    _winmm.waveOutPrepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutPrepareHeader.restype = wintypes.UINT
    _winmm.waveOutUnprepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutUnprepareHeader.restype = wintypes.UINT
    _winmm.waveOutWrite.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutWrite.restype = wintypes.UINT
    _winmm.waveOutReset.argtypes = [ctypes.c_void_p]
    _winmm.waveOutClose.argtypes = [ctypes.c_void_p]
    _winmm.waveOutGetNumDevs.restype = wintypes.UINT
    _winmm.waveInGetNumDevs.restype = wintypes.UINT
except (OSError, AttributeError):
    _winmm = None


def _wave_format(samples_per_sec: int) -> WAVEFORMATEX:
    fmt = WAVEFORMATEX()
    fmt.wFormatTag = WAVE_FORMAT_PCM
    fmt.nChannels = 1
    fmt.nSamplesPerSec = samples_per_sec
    fmt.wBitsPerSample = 16
    fmt.nBlockAlign = 2
    fmt.nAvgBytesPerSec = samples_per_sec * 2
    fmt.cbSize = 0
    return fmt


def resample_pcm(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Interpolacion lineal entre frecuencias de muestreo."""
    if src_rate == dst_rate or not data:
        return data
    source = array.array("h")
    source.frombytes(data)
    count = len(source)
    output_count = round(count * dst_rate / src_rate)
    if output_count == 0:
        return b""
    output = array.array("h")
    ratio = src_rate / dst_rate
    for step in range(output_count):
        position = step * ratio
        index = int(position)
        fraction = position - index
        left = source[index]
        right = source[index + 1] if index + 1 < count else left
        output.append(round(left + (right - left) * fraction))
    return output.tobytes()


class _CaptureLoop:
    """Mantiene vivos buffers y callback de waveIn mientas el dispositivo existe."""

    def __init__(self, samples_per_sec: int, buffer_ms: int = 40) -> None:
        self.samples_per_sec = samples_per_sec
        self.handle = ctypes.c_void_p()
        self.in_queue: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self._buffer_bytes = round(samples_per_sec * buffer_ms / 1000) * 2
        self._callback = WAVE_PROC(self._on_data)
        self._headers: list[WAVEHDR] = []
        self._closing = False

    def open(self) -> None:
        fmt = _wave_format(self.samples_per_sec)
        result = _winmm.waveInOpen(
            ctypes.byref(self.handle),
            WAVE_MAPPER,
            ctypes.byref(fmt),
            self._callback,
            0,
            CALLBACK_FUNCTION,
        )
        if result != MMSYSERR_NOERROR:
            raise AudioError(f"waveInOpen fallo con codigo {result}")
        self._closing = False
        self._prep_all()
        _winmm.waveInStart(self.handle)

    def _prep_all(self) -> None:
        for _ in range(4):
            block = ctypes.create_string_buffer(self._buffer_bytes)
            header = WAVEHDR()
            header.lpData = ctypes.cast(block, ctypes.POINTER(ctypes.c_char))
            header.dwBufferLength = self._buffer_bytes
            header.dwUser = 0
            header.lpNext = None
            _winmm.waveInPrepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
            self._headers.append(header)

    def _queue_header(self, header: WAVEHDR) -> None:
        if self._closing:
            return
        _winmm.waveInAddBuffer(self.handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))

    def _on_data(
        self, _device: ctypes.c_void_p, message: wintypes.UINT, _user: ctypes.c_void_p,
        param1: ctypes.c_void_p, _param2: ctypes.c_void_p,
    ) -> None:
        if message != WIM_DATA:
            return
        if param1:
            header = ctypes.cast(param1, ctypes.POINTER(WAVEHDR)).contents
            length = min(header.dwBytesRecorded, header.dwBufferLength)
            raw = ctypes.string_at(header.lpData, length)
            try:
                self.in_queue.put_nowait(raw)
            except queue.Full:
                pass
            self._queue_header(header)

    def close(self) -> None:
        self._closing = True
        try:
            _winmm.waveInStop(self.handle)
            _winmm.waveInReset(self.handle)
            _winmm.waveInClose(self.handle)
        except OSError:
            pass


class _PlaybackLoop:
    def __init__(self, samples_per_sec: int, buffer_ms: int = 40) -> None:
        self.samples_per_sec = samples_per_sec
        self.handle = ctypes.c_void_p()
        self._buffer_bytes = round(samples_per_sec * buffer_ms / 1000) * 2
        self._callback = WAVE_PROC(self._on_done)
        self._pool: list[tuple[ctypes.Array, WAVEHDR]] = []
        self._closing = False
        self._lock = threading.Lock()

    def open(self) -> None:
        fmt = _wave_format(self.samples_per_sec)
        _winmm.waveOutOpen.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.UINT,
            ctypes.POINTER(WAVEFORMATEX),
            WAVE_PROC,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        _winmm.waveOutOpen.restype = wintypes.UINT
        result = _winmm.waveOutOpen(
            ctypes.byref(self.handle),
            WAVE_MAPPER,
            ctypes.byref(fmt),
            self._callback,
            0,
            CALLBACK_FUNCTION,
        )
        if result != MMSYSERR_NOERROR:
            raise AudioError(f"waveOutOpen fallo con codigo {result}")
        self._closing = False

    def play(self, pcm16: bytes) -> None:
        if self._closing or not pcm16:
            return
        block = ctypes.create_string_buffer(len(pcm16))
        ctypes.memmove(block, pcm16, len(pcm16))
        header = WAVEHDR()
        header.lpData = ctypes.cast(block, ctypes.POINTER(ctypes.c_char))
        header.dwBufferLength = len(pcm16)
        with self._lock:
            self._pool.append((block, header))
        _winmm.waveOutPrepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
        result = _winmm.waveOutWrite(self.handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
        if result != MMSYSERR_NOERROR:
            self._unprepare(header)
            with self._lock:
                self._pool = [(b, h) for b, h in self._pool if ctypes.addressof(h) != ctypes.addressof(header)]
            raise AudioError(f"waveOutWrite fallo con codigo {result}")

    def _unprepare(self, header: WAVEHDR) -> None:
        try:
            _winmm.waveOutUnprepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
        except OSError:
            pass

    def _on_done(
        self, _device: ctypes.c_void_p, message: wintypes.UINT, _user: ctypes.c_void_p,
        param1: ctypes.c_void_p, _param2: ctypes.c_void_p,
    ) -> None:
        if message != WOM_DONE or not param1:
            return
        wanted = int(param1)
        with self._lock:
            surviving: list[tuple[ctypes.Array, WAVEHDR]] = []
            for block, header in self._pool:
                if ctypes.addressof(header) == wanted:
                    self._unprepare(header)
                else:
                    surviving.append((block, header))
            self._pool = surviving

    def close(self) -> None:
        self._closing = True
        handle = self.handle
        self.handle = ctypes.c_void_p()

        def _finalize(_self: _PlaybackLoop) -> None:
            # waveOutReset/Close pueden bloquear mientras el driver devuelve
            # los buffers pendientes; se hacen fuera del hilo que cuelga la
            # llamada para que colgar una llamada o salir de la app nunca se
            # quede congelado en el dispositivo de sonido. `_self` se retiene
            # en el closure para que buffers, headers y callback de winmm
            # sigan vivos hasta que el driver haya devuelto todo.
            time.sleep(0.05)
            try:
                _winmm.waveOutReset(handle)
                _winmm.waveOutClose(handle)
            except OSError:
                pass
            with _self._lock:
                _self._pool.clear()

        threading.Thread(
            target=_finalize, args=(self,), name="waveout-close", daemon=True
        ).start()


class AudioEngine:
    """Mic y altavoz normalizados a 8 kHz. Sin operaciones bloqueantes largas."""

    def __init__(self, input_rate: int | None = None, output_rate: int | None = None) -> None:
        self._capture: _CaptureLoop | None = None
        self._capture_rate = 8000
        self._playback: _PlaybackLoop | None = None
        self._playback_rate = 8000
        self._accumulator = bytearray()
        self._preferred_in = input_rate
        self._preferred_out = output_rate
        self.direction = ""

    def open(self, attempt_input: bool = True, attempt_output: bool = True) -> None:
        if _winmm is None:
            raise AudioError("Audio winmm no disponible fuera de Windows.")
        errors: list[str] = []
        if attempt_input:
            for rate in self._pick_rates(self._preferred_in):
                loop = _CaptureLoop(rate)
                try:
                    loop.open()
                except AudioError as exc:
                    errors.append(f"in@{rate}: {exc}")
                    continue
                self._capture = loop
                self._capture_rate = rate
                break
        if attempt_output:
            for rate in self._pick_rates(self._preferred_out):
                loop = _PlaybackLoop(rate)
                try:
                    loop.open()
                except AudioError as exc:
                    errors.append(f"out@{rate}: {exc}")
                    continue
                self._playback = loop
                self._playback_rate = rate
                break
        parts = []
        if self._capture:
            parts.append(f"mic {self._capture_rate} Hz")
        if self._playback:
            parts.append(f"altavoz {self._playback_rate} Hz")
        if not parts:
            raise AudioError("No hay microfono ni altavoz disponibles: " + " | ".join(errors))
        self.direction = " + ".join(parts)

    def _pick_rates(self, preferred: int | None) -> list[int]:
        if preferred:
            return [preferred] + [r for r in DEFAULT_RATES if r != preferred]
        return list(DEFAULT_RATES)

    @property
    def opened(self) -> bool:
        return self._capture is not None or self._playback is not None

    def read(self, samples: int = FRAME_SAMPLES) -> bytes:
        """Devuelve samples*2 bytes de mic a 8 kHz; silencio si no hay captura."""
        target = samples * 2
        if self._capture is not None:
            try:
                while True:
                    raw = self._capture.in_queue.get_nowait()
                    self._accumulator.extend(resample_pcm(raw, self._capture_rate, 8000))
            except queue.Empty:
                pass
        if len(self._accumulator) >= target:
            frame = bytes(self._accumulator[:target])
            del self._accumulator[:target]
            return frame
        have = len(self._accumulator)
        frame = bytes(self._accumulator) + b"\x00\x00" * ((target - have) // 2)
        self._accumulator.clear()
        return frame

    def play(self, pcm16_8k: bytes) -> None:
        if self._playback is None:
            return
        self._playback.play(resample_pcm(pcm16_8k, 8000, self._playback_rate))

    def prefill(self) -> None:
        if self._playback is not None:
            for _ in range(4):
                self._playback.play(b"\x00\x00" * FRAME_SAMPLES)

    def close(self) -> None:
        try:
            if self._capture is not None:
                self._capture.close()
            if self._playback is not None:
                self._playback.close()
        finally:
            self._capture = None
            self._playback = None


class DummyAudio:
    """Sustituto en memoria para pruebas: silencio en mic, descarta salida."""

    def __init__(self) -> None:
        self.direction = "pruebas"
        self.opened = False
        self.played: list[bytes] = []
        self.mic_silence = True

    def open(self, attempt_input: bool = True, attempt_output: bool = True) -> None:
        self.opened = True

    def read(self, samples: int = FRAME_SAMPLES) -> bytes:
        if self.mic_silence:
            return b"\x00\x00" * samples
        return b"\x01\x00" * samples

    def play(self, pcm16_8k: bytes) -> None:
        self.played.append(bytes(pcm16_8k))

    def prefill(self) -> None:
        return None

    def close(self) -> None:
        self.opened = False