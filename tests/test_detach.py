"""draft_studio.detach: a command as its own transient systemd --user unit."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio import detach


class Run:
    def __init__(self, code: int = 0, raises: Exception | None = None) -> None:
        self.code, self.raises = code, raises
        self.cmd: list[str] = []

    def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.cmd = cmd
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.code, "", "boom")


ENV = {"PATH": "/bin", "HOME": "/h", "DRAFT_STUDIO_DATA": "/d", "SECRET": "x", "DISPLAY": ":9"}


def test_the_unit_is_collected_named_and_gets_only_the_listed_environment(tmp_path: Path) -> None:
    run = Run()
    log = tmp_path / "out.log"
    detach.systemd_spawn("ds-call-a", ["python3", "-m", "x"], log, cwd=tmp_path, run=run, environ=ENV)
    cmd = run.cmd
    assert cmd[:3] == ["systemd-run", "--user", "--collect"]
    assert "--unit=ds-call-a" in cmd and f"--working-directory={tmp_path}" in cmd
    assert "--setenv=DRAFT_STUDIO_DATA=/d" in cmd and "--setenv=DISPLAY=:9" in cmd
    assert not any("SECRET" in a for a in cmd)
    assert f"--property=StandardOutput=append:{log}" in cmd
    assert cmd[cmd.index("--") + 1 :] == ["python3", "-m", "x"]


def test_no_log_means_no_output_property() -> None:
    run = Run()
    detach.systemd_spawn("u", ["true"], None, run=run, environ={})
    assert not any(a.startswith("--property") for a in run.cmd)


@pytest.mark.parametrize(
    "run",
    [Run(code=1), Run(raises=OSError("no systemd-run")), Run(raises=subprocess.TimeoutExpired("x", 1))],
)
def test_a_unit_that_did_not_start_is_a_loud_error(run: Run) -> None:
    with pytest.raises(detach.DetachError):
        detach.systemd_spawn("u", ["true"], None, run=run, environ={})
