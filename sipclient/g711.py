"""Códec G.711 en Python puro (mu-law y A-law).

Python 3.13 eliminó el módulo `audioop`, así que se reconstruyen las tablas
clásicas de compansion ITU-T G.711 con el algoritmo Sun Reference (el mismo
que usaba audioop) para no depender de bibliotecas de terceros.
"""

from __future__ import annotations

import array

SIGN_BIT = 0x80
QUANT_MASK = 0x0F
SEG_SHIFT = 4
SEG_MASK = 0x70
BIAS = 0x84

_U_SEG_END = (0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF)
_A_SEG_AEND = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)

_CLIP_U = 0x7FFF
_CLIP_A = 0xFFF


# ---------------------------------------------------------------------------
# mu-law
# ---------------------------------------------------------------------------

def _linear2ulaw(pcm: int) -> int:
    sign = (pcm >> 8) & SIGN_BIT
    if sign:
        pcm = -pcm
    if pcm > _CLIP_U:
        pcm = _CLIP_U
    exponent = 0
    for exponent, limit in enumerate(_U_SEG_END):
        if pcm <= limit:
            break
    mantissa = (pcm >> (exponent + 3)) & QUANT_MASK
    return ~(sign | (exponent << SEG_SHIFT) | mantissa) & 0xFF


def _ulaw2linear(byte: int) -> int:
    value = ~byte & 0xFF
    sign = value & SIGN_BIT
    exponent = (value & SEG_MASK) >> SEG_SHIFT
    mantissa = value & QUANT_MASK
    magnitude = ((mantissa << 3) + BIAS) << exponent
    return BIAS - magnitude if sign else magnitude - BIAS


_LIN2ULAW = tuple(_linear2ulaw(value) for value in range(-32768, 32768))
_ULAW2LIN = tuple(_ulaw2linear(value) for value in range(256))
_OFFSET = 32768


# ---------------------------------------------------------------------------
# A-law
# ---------------------------------------------------------------------------

def _linear2alaw(pcm: int) -> int:
    pcm >>= 3
    if pcm >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        pcm = -pcm - 1
    if pcm > _CLIP_A:
        return 0x7F ^ mask
    segment = 0
    for segment, limit in enumerate(_A_SEG_AEND):
        if pcm <= limit:
            break
    else:
        return 0x7F ^ mask
    if segment < 2:
        quantized = (pcm >> 1) & QUANT_MASK
    else:
        quantized = (pcm >> segment) & QUANT_MASK
    return ((segment << SEG_SHIFT) | quantized) ^ mask


def _alaw2linear(byte: int) -> int:
    value = byte ^ 0x55
    magnitude = (value & QUANT_MASK) << 4
    segment = (value & SEG_MASK) >> SEG_SHIFT
    if segment >= 2:
        magnitude = (magnitude + 0x108) << (segment - 1)
    elif segment == 1:
        magnitude += 0x108
    else:
        magnitude += 8
    return magnitude if value & 0x80 else -magnitude


_LIN2ALAW = tuple(_linear2alaw(value) for value in range(-32768, 32768))
_ALAW2LIN = tuple(_alaw2linear(value) for value in range(256))


# ---------------------------------------------------------------------------
# APIs publicas (bytes de 8 kHz, mono, 16 bits little-endian)
# ---------------------------------------------------------------------------

def ulaw_encode(pcm: bytes) -> bytes:
    """Codifica PCM 16-bit little-endian a bytes mu-law."""
    samples = memoryview(pcm).cast("h")
    return bytes(_LIN2ULAW[sample + _OFFSET] for sample in samples)


def ulaw_decode(encoded: bytes) -> bytes:
    """Decodifica bytes mu-law a PCM 16-bit little-endian."""
    return array.array("h", (_ULAW2LIN[byte] for byte in encoded)).tobytes()


def alaw_encode(pcm: bytes) -> bytes:
    """Codifica PCM 16-bit little-endian a bytes A-law."""
    samples = memoryview(pcm).cast("h")
    return bytes(_LIN2ALAW[sample + _OFFSET] for sample in samples)


def alaw_decode(encoded: bytes) -> bytes:
    """Decodifica bytes A-law a PCM 16-bit little-endian."""
    return array.array("h", (_ALAW2LIN[byte] for byte in encoded)).tobytes()


# Alias usados por el motor RTP.
ENCODERS = {"PCMU": ulaw_encode, "PCMA": alaw_encode}
DECODERS = {"PCMU": ulaw_decode, "PCMA": alaw_decode}