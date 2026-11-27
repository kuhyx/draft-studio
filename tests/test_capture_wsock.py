"""The stdlib WebSocket client against a loopback fake server."""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Callable, Iterator

import pytest

from draft_studio.capture import wsock

Script = Callable[[socket.socket, bytes], None]


def read_frame(conn: socket.socket) -> tuple[int, bytes]:
    head = conn.recv(2)
    n = head[1] & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", conn.recv(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", conn.recv(8))
    key = conn.recv(4)
    data = b""
    while len(data) < n:
        data += conn.recv(n - len(data))
    return head[0] & 0x0F, bytes(b ^ key[i % 4] for i, b in enumerate(data))


@pytest.fixture
def serve() -> Iterator[Callable[..., str]]:
    threads: list[threading.Thread] = []

    def start(script: Script, answer: Callable[[bytes], bytes] | None = None) -> str:
        srv = socket.create_server(("127.0.0.1", 0))

        def body() -> None:
            conn, _ = srv.accept()
            req = b""
            while b"\r\n\r\n" not in req:
                req += conn.recv(4096)
            key = next(
                line.split(b": ")[1] for line in req.split(b"\r\n") if line.startswith(b"Sec-WebSocket-Key")
            )
            reply = (
                answer(key)
                if answer
                else (
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"Sec-WebSocket-Accept: " + wsock.accept_key(key).encode() + b"\r\n\r\n"
                )
            )
            conn.sendall(reply)
            script(conn, key)
            conn.close()
            srv.close()

        t = threading.Thread(target=body, daemon=True)
        t.start()
        threads.append(t)
        return f"ws://127.0.0.1:{srv.getsockname()[1]}/devtools/browser/x"

    yield start
    for t in threads:
        t.join(timeout=5)


def test_round_trip_all_lengths_masks_fragments_and_ping(serve: Callable[..., str]) -> None:
    got: list[tuple[int, bytes]] = []

    def script(conn: socket.socket, _key: bytes) -> None:
        got.append(read_frame(conn))
        conn.sendall(wsock.frame(0x1, b"short"))
        conn.sendall(wsock.frame(0x1, b"m" * 300))
        conn.sendall(wsock.frame(0x2, b"b" * 70000))
        conn.sendall(wsock.frame(0x1, b"masked", b"\x01\x02\x03\x04"))
        conn.sendall(bytes([0x01, 3]) + b"abc" + wsock.frame(0x9, b"hi") + wsock.frame(0x0, b"def"))
        got.append(read_frame(conn))
        conn.sendall(wsock.frame(0xA, b"") + wsock.frame(0x8, b""))

    ws = wsock.connect(serve(script), timeout=5)
    ws.send("x" * 200)
    assert [ws.recv(), len(ws.recv()), len(ws.recv()), ws.recv(), ws.recv()] == [
        "short",
        300,
        70000,
        "masked",
        "abcdef",
    ]
    with pytest.raises(wsock.WsClosed):
        ws.recv()
    ws.close()
    assert got == [(0x1, b"x" * 200), (0xA, b"hi")]


def test_big_client_frame(serve: Callable[..., str]) -> None:
    got: list[tuple[int, bytes]] = []
    done = threading.Event()

    def script(conn: socket.socket, _key: bytes) -> None:
        got.append(read_frame(conn))
        done.set()

    ws = wsock.connect(serve(script), timeout=5)
    ws.send("y" * 70000)
    assert done.wait(5)
    ws.close()
    assert got == [(0x1, b"y" * 70000)]


def test_readable_and_eof(serve: Callable[..., str]) -> None:
    gate = threading.Event()

    def script(conn: socket.socket, _key: bytes) -> None:
        gate.wait(5)
        conn.sendall(bytes([0x81, 10]) + b"cut")

    ws = wsock.connect(serve(script), timeout=5)
    assert ws.readable(0.05) is False
    gate.set()
    assert ws.readable(5) is True
    with pytest.raises(wsock.WsClosed):
        ws.recv()


def test_frames_sent_with_the_handshake_are_kept(serve: Callable[..., str]) -> None:
    def answer(key: bytes) -> bytes:
        ok = b"HTTP/1.1 101 OK\r\nSec-WebSocket-Accept: " + wsock.accept_key(key).encode() + b"\r\n\r\n"
        return ok + wsock.frame(0x1, b"early")

    ws = wsock.connect(serve(lambda c, k: None, answer), timeout=5)
    assert ws.readable(0) is True and ws.recv() == "early"


@pytest.mark.parametrize(
    "answer",
    [
        lambda key: b"HTTP/1.1 403 Forbidden\r\n\r\n",
        lambda key: b"HTTP/1.1 101 OK\r\nSec-WebSocket-Accept: wrong\r\n\r\n",
        lambda key: b"HTTP/1.1 101 OK\r\n",
        lambda key: b"HTTP/1.1 101 OK\r\nX: " + b"a" * 20000,
    ],
)
def test_bad_handshakes(serve: Callable[..., str], answer: Callable[[bytes], bytes]) -> None:
    with pytest.raises(wsock.WsClosed):
        wsock.connect(serve(lambda c, k: None, answer), timeout=5)
