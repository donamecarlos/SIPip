"""Captura y reproduccion de audio en Android via MediaRecorder/AudioTrack.

Usa pyjnius (python-for-android) para acceder a AudioRecord y AudioTrack a
8 kHz, PCM 16-bit mono, con el modo de voz IN_COMMUNICATION para aprovechar el
procesamiento de voz del sistema. Implementa el mismo contrato que
`sipclient.audio.AudioEngine` para que el motor de llamadas sea plug-and-play.
"""

from __future__ import annotations

import threading


class AndroidAudioError(OSError):
    pass


def _jnius():
    from jnius import autoclass  # type: ignore[attr-defined]

    audio_format = autoclass("android.media.AudioFormat")
    audio_record = autoclass("android.media.AudioRecord")
    audio_track = autoclass("android.media.AudioTrack")
    audio_manager = autoclass("android.media.AudioManager")
    media_recorder = autoclass("android.media.MediaRecorder")
    python_activity = autoclass("org.kivy.android.PythonActivity")

    activity = python_activity.mActivity
    manager = activity.getSystemService(activity.AUDIO_SERVICE)
    return {
        "AudioFormat": audio_format,
        "AudioRecord": audio_record,
        "AudioTrack": audio_track,
        "AudioManager": audio_manager,
        "MediaRecorder": media_recorder,
        "manager": manager,
    }


_SAMPLE_RATE = 8000
_FRAME_SAMPLES = 160
_CHANNEL_IN_MONO = 16  # AudioFormat.CHANNEL_IN_MONO
_CHANNEL_OUT_MONO = 12  # AudioFormat.CHANNEL_OUT_MONO
_PCM16 = 2  # AudioFormat.ENCODING_PCM_16BIT
_VOICE_COMMUNICATION = 7  # MediaRecorder.AudioSource.VOICE_COMMUNICATION
_MODE_IN_COMMUNICATION = 3  # AudioManager.MODE_IN_COMMUNICATION


class _AndroidCapture:
    def __init__(self) -> None:
        self._record = None
        self._buffer_size = _SAMPLE_RATE * 2  # 1 segundo, margen amplio
        self._lock = threading.Lock()

    def open(self) -> None:
        from jnius import jarray  # type: ignore[attr-defined]

        api = _jnius()
        self._buffer_size = max(
            self._buffer_size,
            api["AudioRecord"].getMinBufferSize(
                _SAMPLE_RATE, _CHANNEL_IN_MONO, _PCM16
            ),
        )
        with self._lock:
            record = api["AudioRecord"](
                _VOICE_COMMUNICATION, _SAMPLE_RATE, _CHANNEL_IN_MONO, _PCM16,
                self._buffer_size,
            )
            if record.getState() != 1:  # STATE_INITIALIZED
                raise AndroidAudioError("AudioRecord no inicializado.")
            self._record = record
            self._jarray = jarray("b", _FRAME_SAMPLES * 2)
        record.startRecording()

    def read_blocking(self, samples: int) -> bytes:
        record = self._record
        if record is None:
            return b"\x00\x00" * samples
        target = samples * 2
        try:
            buffer = self._jarray
            if len(buffer) < target:
                from jnius import jarray  # type: ignore[attr-defined]

                buffer = jarray("b", target)
                self._jarray = buffer
            total = 0
            while total < target:
                read = record.read(buffer, total, target - total)
                if read <= 0:
                    break
                total += read
            return bytes(buffer[:total]) + b"\x00\x00" * (target - total)
        except Exception:  # noqa: BLE001 - el dispositivo pudo cerrarse a la vez
            return b"\x00\x00" * samples

    def close(self) -> None:
        with self._lock:
            record = self._record
            self._record = None
        if record is not None:
            try:
                record.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                record.release()
            except Exception:  # noqa: BLE001
                pass


class _AndroidPlayback:
    def __init__(self) -> None:
        self._track = None
        self._lock = threading.Lock()

    def open(self) -> None:
        api = _jnius()
        self._buffer_size = max(
            _SAMPLE_RATE * 2,
            api["AudioTrack"].getMinBufferSize(
                _SAMPLE_RATE, _CHANNEL_OUT_MONO, _PCM16
            ),
        )
        manager = api["manager"]
        try:
            manager.setMode(_MODE_IN_COMMUNICATION)
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            track = api["AudioTrack"](
                api["AudioManager"].STREAM_VOICE_CALL,
                _SAMPLE_RATE,
                _CHANNEL_OUT_MONO,
                _PCM16,
                self._buffer_size,
                api["AudioTrack"].MODE_STREAM,
            )
            if track.getState() != 1:
                raise AndroidAudioError("AudioTrack no inicializado.")
            self._track = track
        track.setVolume(1.0)
        track.play()

    def play(self, pcm16: bytes) -> None:
        track = self._track
        if track is None or not pcm16:
            return
        try:
            remaining = memoryview(pcm16)
            while remaining:
                written = track.write(bytearray(remaining), len(remaining),
                                      _WRITE_BLOCKING)
                if written <= 0:
                    break
                remaining = remaining[written:]
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        with self._lock:
            track = self._track
            self._track = None
        if track is not None:
            try:
                track.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                track.release()
            except Exception:  # noqa: BLE001
                pass


_WRITE_BLOCKING = 1  # AudioTrack.WRITE_BLOCKING


class AndroidAudioEngine:
    """Mic y altavoz en Android normalizados a 8 kHz.

    `read()` bloquea hasta tener una trama completa (silencio si no hay
    captura) y `play()` bloquesa mientras el sistema consume el PCM, por lo
    que el hilo de medios del motor marca el ritmo de la llamada.
    """

    def __init__(self, input_rate: int | None = None, output_rate: int | None = None) -> None:
        self._capture: _AndroidCapture | None = None
        self._playback: _AndroidPlayback | None = None
        self._accumulator = bytearray()
        self.direction = ""

    @property
    def opened(self) -> bool:
        return self._capture is not None or self._playback is not None

    def open(self, attempt_input: bool = True, attempt_output: bool = True) -> None:
        errors: list[str] = []
        if attempt_input:
            capture = _AndroidCapture()
            try:
                capture.open()
            except AndroidAudioError as exc:
                errors.append(f"mic: {exc}")
            else:
                self._capture = capture
        if attempt_output:
            playback = _AndroidPlayback()
            try:
                playback.open()
            except AndroidAudioError as exc:
                errors.append(f"altavoz: {exc}")
            else:
                self._playback = playback
        parts = []
        if self._capture:
            parts.append("mic 8000 Hz")
        if self._playback:
            parts.append("altavoz 8000 Hz")
        if not parts:
            raise AndroidAudioError(
                "No hay microfono ni altavoz disponibles: " + " | ".join(errors)
            )
        self.direction = " + ".join(parts)

    def read(self, samples: int = _FRAME_SAMPLES) -> bytes:
        target = samples * 2
        if self._capture is not None:
            pending = self._capture.read_blocking(target)
            self._accumulator.extend(pending)
        if len(self._accumulator) >= target:
            frame = bytes(self._accumulator[:target])
            del self._accumulator[:target]
            return frame
        have = len(self._accumulator)
        frame = bytes(self._accumulator) + b"\x00\x00" * ((target - have) // 2)
        self._accumulator.clear()
        return frame

    def play(self, pcm16_8k: bytes) -> None:
        if self._playback is not None:
            self._playback.play(bytes(pcm16_8k))

    def prefill(self) -> None:
        if self._playback is not None:
            for _ in range(4):
                self._playback.play(b"\x00\x00" * _FRAME_SAMPLES)

    def close(self) -> None:
        try:
            if self._capture is not None:
                self._capture.close()
            if self._playback is not None:
                self._playback.close()
        finally:
            self._capture = None
            self._playback = None