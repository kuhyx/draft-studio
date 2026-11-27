"""Decode chromium's screenshots (8-bit RGB/RGBA, non-interlaced PNG) with zlib alone."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


_LOW = {}


def _masks(n: int) -> tuple[int, int]:
    """(0x7f.. , 0x80..) over n bytes, for the byte-wise modular add."""
    if n not in _LOW:
        _LOW[n] = (int.from_bytes(b"\x7f" * n, "little"), int.from_bytes(b"\x80" * n, "little"))
    return _LOW[n]


def _add_rows(a: bytes, b: bytes) -> bytes:
    """Byte-wise (a + b) mod 256 of two equal-length rows, as one big-int add (no carry between bytes)."""
    low, high = _masks(len(a))
    x, y = int.from_bytes(a, "little"), int.from_bytes(b, "little")
    return (((x & low) + (y & low)) ^ ((x ^ y) & high)).to_bytes(len(a), "little")


def _unfilter(kind: int, raw: bytes, prev: bytes, bpp: int) -> bytes:
    """One scanline back to pixels. Up is a vector add; the other kinds depend on the left byte."""
    if kind == 0:
        return raw
    if kind == 2:
        return _add_rows(raw, prev)
    line = bytearray(raw)
    for i in range(len(line)):
        a = line[i - bpp] if i >= bpp else 0
        b, c = prev[i], prev[i - bpp] if i >= bpp else 0
        if kind == 1:
            line[i] = (line[i] + a) & 0xFF
        elif kind == 3:
            line[i] = (line[i] + (a + b) // 2) & 0xFF
        else:
            line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
    return bytes(line)


def decode(data: bytes) -> tuple[int, int, int, bytes]:
    """(width, height, bytes per pixel, raw pixel rows without filter bytes)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    width = height = bpp = 0
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + n]
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and interlace == 0 and color in (2, 6), (depth, color, interlace)
            bpp = 3 if color == 2 else 4
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows: list[bytes] = []
    prev = bytes(stride)
    # Screenshots are mostly repeated rows: the same filtered row over the same row above gives the same pixels.
    memo: dict[tuple[bytes, bytes], bytes] = {}
    for row in range(height):
        start = row * (stride + 1)
        key = (raw[start : start + 1 + stride], prev)
        line = memo.get(key)
        if line is None:
            line = memo[key] = _unfilter(key[0][0], key[0][1:], prev, bpp)
        rows.append(line)
        prev = line
    return width, height, bpp, b"".join(rows)


def _count(data: bytes, hit: Callable[[int, int, int], bool]) -> tuple[int, int, int]:
    """(width, height, pixels satisfying `hit`), counting each distinct row once."""
    width, height, bpp, px = decode(data)
    stride, seen, total = width * bpp, {}, 0
    for row in range(height):
        line = px[row * stride : (row + 1) * stride]
        if line not in seen:
            seen[line] = sum(1 for i in range(0, stride, bpp) if hit(line[i], line[i + 1], line[i + 2]))
        total += seen[line]
    return width, height, total


def red_pixels(data: bytes) -> tuple[int, int, int]:
    """(width, height, count of pure-ish red pixels: r > 200, g < 60, b < 60)."""
    return _count(data, lambda r, g, b: r > 200 and g < 60 and b < 60)


def green_share(data: bytes) -> float:
    """Share of pixels in the new-tab test page's green background (rgb(20, 140, 60), give or take)."""
    width, height, hits = _count(data, lambda r, g, b: r < 50 and 120 < g < 160 and b < 90)
    return hits / (width * height)
