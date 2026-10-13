"""Genera icon.png (512x512) para el build Android sin dependencias."""

import struct
import zlib


def _png(width, height, pixels):
    def chunk(tag, data):
        payload = tag + data
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", crc)

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixels(x, y))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def make_icon():
    size = 512
    cx = cy = size / 2
    radius = 380 / 2

    def pixel(x, y):
        dx, dy = x - cx, y - cy
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= radius:
            # circulo verde de llamada
            return (30, 180, 84, 255)
        if dist <= radius + 4:
            return (18, 40, 90, 255)
        return (18, 28, 48, 255)

    return _png(size, size, pixel)


if __name__ == "__main__":
    from pathlib import Path

    Path("icon.png").write_bytes(make_icon())
    print("icon.png escrito")