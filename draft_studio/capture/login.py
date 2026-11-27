"""`python3 -m draft_studio.capture login <url>`: kuhy logs into a site once, by hand.

Opens a headed chromium window on the capture profile (same --user-data-dir and cookie key as
the headless captures), so the session cookies persist. Claude never sees a password: kuhy types
it. The window closes by itself once the login is done (loginwatch.py: it left the login page and
stayed off it), after 10 minutes at the latest; closing it by hand works too. While the window
is open the profile is locked and captures answer profile-busy.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from draft_studio.capture.browser import PROFILE_FLAGS, chromium_path, holder, profile_dir, read_endpoint
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.cookiejar import CookieJar
from draft_studio.capture.loginwatch import CALL_S, LoginWatch
from draft_studio.capture.policy import is_login_page, origin
from draft_studio.capture.private import ensure_private
from draft_studio.capture.wsock import WsClosed, connect
from draft_studio.store import default_data_dir

START_S = 30.0  # how long chromium may take to publish its DevTools port
CLOSE_S = 5.0  # how long a told-to-close chromium may take before it is killed
Watch = Callable[[Any, Path, str], str]  # (process, profile, the URL the window opened on)


def login_argv(binary: str, profile: str, url: str, extra: tuple[str, ...] = ()) -> list[str]:
    """The window's command line; chromium picks the DevTools port (written to the profile)."""
    return [
        binary, *PROFILE_FLAGS, *extra, "--mute-audio", "--remote-debugging-port=0",
        f"--user-data-dir={profile}", "--new-window", url,
    ]  # fmt: skip


def watch_window(
    proc: Any,
    profile: Path,
    url: str,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    **tuning: float,
) -> str:
    """Watch `proc`'s window until it is done: "logged-in", "timeout", "closed" or "unwatched"."""
    deadline = clock() + START_S
    while (endpoint := read_endpoint(profile)) is None:
        if proc.poll() is not None:
            return "closed"
        if clock() >= deadline:
            return "unwatched"
        sleep(0.05)
    try:
        ws = connect(endpoint, CALL_S)
    except (OSError, WsClosed):
        return "unwatched"
    cdp = Cdp(ws, clock() + CALL_S, clock)
    try:
        watch = LoginWatch(
            cdp,
            origin(url),
            CookieJar(profile),
            clock=clock,
            sleep=sleep,
            alive=lambda: proc.poll() is None,
            seen_login=is_login_page(url, False),
            **tuning,
        )
        return watch.run()
    finally:
        cdp.close()


def open_window(
    url: str,
    profile: Path,
    binary: str,
    *,
    extra: tuple[str, ...] = (),
    popen: Callable[..., Any] = subprocess.Popen,
    watch: Watch = watch_window,
) -> str:
    """Run the window to its end; the watch's outcome. `extra`: more chromium flags (tests: headless)."""
    ensure_private(profile)
    (profile / "DevToolsActivePort").unlink(missing_ok=True)
    proc = popen(login_argv(binary, str(profile), url, extra))
    outcome = watch(proc, profile, url)
    if outcome == "unwatched":
        print("login: no DevTools connection, close the window yourself when logged in", file=sys.stderr)
    try:
        proc.wait(timeout=None if outcome == "unwatched" else CLOSE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return outcome


def main(argv: list[str] | None = None, **window: Any) -> int:
    """Exit 0 after the window closed; 2 bad URL or profile busy; 3 no chromium."""
    parser = argparse.ArgumentParser(prog="python3 -m draft_studio.capture")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="log into a site in a window on the capture profile").add_argument("url")
    args = parser.parse_args(argv)
    if urlsplit(args.url).scheme not in ("http", "https"):
        print(f"login: not an http(s) URL: {args.url}", file=sys.stderr)
        return 2
    profile = profile_dir(default_data_dir())
    pid = holder(profile)
    if pid is not None:
        print(f"login: capture profile in use by pid {pid}: {profile}", file=sys.stderr)
        return 2
    binary = chromium_path()
    if not os.access(binary, os.X_OK):
        print(f"login: no chromium at {binary}", file=sys.stderr)
        return 3
    print(f"login: log in to {args.url}; the window closes by itself. Profile: {profile}")
    open_window(args.url, profile, binary, **window)
    return 0
