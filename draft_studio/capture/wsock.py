"""A minimal RFC 6455 WebSocket client (stdlib only) for chromium's DevTools endpoint.

Text frames out (masked, as clients must), text/binary frames in, fragments
joined, pings answered. No Origin header is sent, which chromium accepts.
"""

from __future__ import annotations

import base64
import hashlib
import os
import select
import socket
import struct
from urllib.parse import urlsplit

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_TEXT, _BINARY, _CLOSE, _PING, _PONG, _CONT = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0
_MAX_HEADER = 16384


class WsClosed(ConnectionError):
    """The peer closed the connection or broke the protocol."""


def accept_key(key: bytes) -> str:
    """The Sec-WebSocket-Accept value a server must answer `key` with."""
    return base64.b64encode(hashlib.sha1(key + _GUID).digest()).decode()


def _mask(payload: bytes, key: bytes) -> bytes:
    n = len(payload)
    whole = int.from_bytes(payload, "big") ^ int.from_bytes((key * (n // 4 + 1))[:n], "big")
    return whole.to_bytes(n, "big")


def frame(opcode: int, payload: bytes, mask: bytes | None = None) -> bytes:
    """One FIN frame; masked when `mask` (4 bytes) is given."""
    n = len(payload)
    bit = 0x80 if mask is not None else 0
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, bit | n)
    elif n < 1 << 16:
        head = struct.pack("!BBH", 0x80 | opcode, bit | 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, bit | 127, n)
    if mask is None:
        return head + payload
    return head + mask + _mask(payload, mask)


class WebSocket:
    """A connected client socket; `buf` holds bytes read past the handshake."""

    def __init__(self, sock: socket.socket, buf: bytes = b"") -> None:
        self.sock = sock
        self._buf = bytearray(buf)

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise WsClosed("eof")
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _send(self, opcode: int, payload: bytes) -> None:
        self.sock.sendall(frame(opcode, payload, os.urandom(4)))

    def send(self, text: str) -> None:
        """Send one text message."""
        self._send(_TEXT, text.encode())

    def readable(self, timeout: float) -> bool:
        """True when a frame can be read now (buffered or on the socket) within `timeout`."""
        if self._buf:
            return True
        ready, _, _ = select.select([self.sock], [], [], max(timeout, 0))
        return bool(ready)

    def recv(self) -> str:
        """The next whole text/binary message (the socket timeout bounds each read)."""
        parts: list[bytes] = []
        while True:
            b0, b1 = self._read(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", self._read(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", self._read(8))
            key = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if key is not None:
                payload = _mask(payload, key)
            if opcode == _PING:
                self._send(_PONG, payload)
                continue
            if opcode == _CLOSE:
                raise WsClosed("closed by peer")
            if opcode in (_TEXT, _BINARY, _CONT):
                parts.append(payload)
            if b0 & 0x80 and parts:
                return b"".join(parts).decode()

    def close(self) -> None:
        """Close the socket (no close handshake: chromium is killed right after)."""
        self.sock.close()


def connect(url: str, timeout: float) -> WebSocket:
    """Open ws://host:port/path and finish the opening handshake."""
    parts = urlsplit(url)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16))
    request = (
        f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key.decode()}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk or len(data) > _MAX_HEADER:
            sock.close()
            raise WsClosed("handshake")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.decode(errors="replace").split("\r\n")
    headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    if " 101 " not in lines[0] + " " or headers.get("sec-websocket-accept") != accept_key(key):
        sock.close()
        raise WsClosed("handshake")
    return WebSocket(sock, rest)
