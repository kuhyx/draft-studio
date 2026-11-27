"""A client that resets the connection while the server reads is debug noise, not a traceback."""

from __future__ import annotations

import http.client
import logging
import socket
import struct
import threading
from collections.abc import Callable

import pytest

from draft_studio import server as srv
from tests.conftest import App

WAIT = 5.0  # seconds to wait for the server thread to finish a request


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Set once the server's handle_error ran, so a test knows the request thread is done."""
    done = threading.Event()
    original: Callable[..., None] = srv._Server.handle_error

    def wrapped(self: srv._Server, request: object, client_address: object) -> None:
        original(self, request, client_address)
        done.set()

    monkeypatch.setattr(srv._Server, "handle_error", wrapped)
    return done


def _reset_after(app: App, partial: bytes) -> None:
    """Send `partial`, then close with SO_LINGER 0 so the kernel sends RST instead of FIN."""
    sock = socket.create_connection(("127.0.0.1", app.httpd.server_address[1]))
    sock.sendall(partial)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def test_reset_while_server_reads_logs_no_traceback(
    app: App, handled: threading.Event, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="draft_studio")
    _reset_after(app, b"GET /vers")
    assert handled.wait(WAIT)
    app.call("GET", "/version")  # the server is still serving afterwards
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "ConnectionResetError" not in err
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_reset_is_reported_at_debug(
    app: App, handled: threading.Event, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="draft_studio")
    _reset_after(app, b"GET /vers")
    assert handled.wait(WAIT)
    assert [r for r in caplog.records if r.levelno == logging.DEBUG and "client" in r.getMessage()]


def test_real_error_still_prints_traceback(
    app: App,
    handled: threading.Event,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: srv.Handler) -> None:
        raise RuntimeError("real bug")

    monkeypatch.setattr(srv.Handler, "do_GET", boom)
    conn = http.client.HTTPConnection("127.0.0.1", app.httpd.server_address[1], timeout=WAIT)
    conn.request("GET", "/version")
    with pytest.raises((http.client.RemoteDisconnected, ConnectionError)):
        conn.getresponse()
    assert handled.wait(WAIT)
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "RuntimeError: real bug" in err
