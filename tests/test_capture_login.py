"""The one-time login command: headed window on the capture profile, refused while it is held."""

from __future__ import annotations

import os
import runpy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture import login


@pytest.fixture
def chromium(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "chromium"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(binary))
    return binary


def profile() -> Path:
    return Path(os.environ["DRAFT_STUDIO_DATA"]) / "capture-profile"


def test_opens_a_headed_window_on_the_capture_profile(
    chromium: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], check: bool) -> Any:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    assert login.main(["login", "https://panel.example/"], run=run) == 0
    [argv] = calls
    assert argv[0] == str(chromium) and argv[-1] == "https://panel.example/"
    assert f"--user-data-dir={profile()}" in argv and "--password-store=basic" in argv
    assert "--mute-audio" in argv
    assert not any(a.startswith("--headless") or a.startswith("--remote-debugging") for a in argv)
    assert "close the window" in capsys.readouterr().out


def test_refusals(chromium: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert login.main(["login", "file:///etc"]) == 2
    profile().mkdir(parents=True)
    os.symlink(f"host-{os.getpid()}", profile() / "SingletonLock")
    assert login.main(["login", "https://x/"]) == 2
    assert "in use" in capsys.readouterr().err
    (profile() / "SingletonLock").unlink()
    chromium.unlink()
    assert login.main(["login", "https://x/"]) == 3


def test_module_entry_point(chromium: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["draft_studio.capture", "login", "https://x/"])
    with pytest.raises(SystemExit) as done:
        runpy.run_module("draft_studio.capture", run_name="__main__")
    assert done.value.code == 0
