"""The PNG decoder behind the capture-live pixel checks: every scanline filter, round trip."""

from __future__ import annotations

import random
import struct
import zlib

import pytest

from tests.capture_live import pngread
from tests.capture_live.pngread import _paeth, green_share, red_pixels

BPP = 3


def _filter(kind: int, line: bytes, prev: bytes) -> bytes:
    """The PNG encoder side of one scanline."""
    out = bytearray()
    for i, x in enumerate(line):
        a = line[i - BPP] if i >= BPP else 0
        b, c = prev[i], prev[i - BPP] if i >= BPP else 0
        pred = (0, a, b, (a + b) // 2, _paeth(a, b, c))[kind]
        out.append((x - pred) & 0xFF)
    return bytes([kind]) + bytes(out)


def _png(rows: list[bytes], kinds: list[int]) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    width = len(rows[0]) // BPP
    prev = bytes(len(rows[0]))
    raw = b""
    for line, kind in zip(rows, kinds, strict=True):
        raw += _filter(kind, line, prev)
        prev = line
    head = struct.pack(">IIBBBBB", width, len(rows), 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("seed", range(5))
def test_decode_round_trips_every_filter(seed: int) -> None:
    rng = random.Random(seed)
    rows = [bytes(rng.randrange(256) for _ in range(7 * BPP)) for _ in range(10)]
    kinds = [rng.randrange(5) for _ in rows] + [0, 1, 2, 3, 4]
    rows += [rows[0]] * 5  # repeated rows exercise the memo
    width, height, bpp, px = pngread.decode(_png(rows, kinds[: len(rows)]))
    assert (width, height, bpp, px) == (7, len(rows), BPP, b"".join(rows))


def test_counts() -> None:
    red, green, blank = bytes([255, 0, 0]) * 2, bytes([20, 140, 60]) * 2, bytes([255, 255, 255]) * 2
    data = _png([red, green, blank, red], [2, 2, 2, 4])
    assert red_pixels(data) == (2, 4, 4)
    assert green_share(data) == 0.25
