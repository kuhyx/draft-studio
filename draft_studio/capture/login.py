"""`python3 -m draft_studio.capture login <url>`: kuhy logs into a site once, by hand.

Opens a headed chromium window on the capture profile (same --user-data-dir
and cookie key as the headless captures), so the session cookies persist.
Claude never sees a password: kuhy types it, then closes the window. While the
window is open the profile is locked and captures answer profile-busy.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from draft_studio.capture.browser import PROFILE_FLAGS, chromium_path, holder, profile_dir
from draft_studio.capture.private import ensure_private
from draft_studio.store import default_data_dir


def login_argv(binary: str, profile: str, url: str) -> list[str]:
    """The headed window's command line."""
    return [binary, *PROFILE_FLAGS, "--mute-audio", f"--user-data-dir={profile}", "--new-window", url]


def main(argv: list[str] | None = None, run: Callable[..., Any] = subprocess.run) -> int:
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
    ensure_private(profile)
    print(f"login: log in to {args.url}, then close the window. Profile: {profile}")
    code: int = run(login_argv(binary, str(profile), args.url), check=False).returncode
    return code
