"""Start a command as its own transient `systemd --user` unit, so no restart of the server can stop it.

`start_new_session` is not enough: the draft-studio unit stops its whole cgroup (KillMode
control-group) on every restart or deploy, session or not. A unit started with `systemd-run` lives
in a cgroup of its own and is collected by systemd when it ends.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# What a detached command may inherit from the server: tools, the user's session, our own settings.
PASS_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)
START_TIMEOUT_S = 20.0

Spawn = Callable[[str, list[str], Path | None], None]  # (unit name, argv, log file)


class DetachError(RuntimeError):
    """The unit could not be started (no systemd-run, name taken, user manager down)."""


def systemd_spawn(
    unit: str,
    argv: list[str],
    log: Path | None,
    *,
    cwd: Path = REPO,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environ: Mapping[str, str] = os.environ,
) -> None:
    """Start `argv` as unit `unit` (output appended to `log`); returns once the unit is started."""
    env = [f"--setenv={k}={v}" for k, v in environ.items() if k in PASS_ENV or k.startswith("DRAFT_STUDIO_")]
    out = [f"--property={s}=append:{log}" for s in ("StandardOutput", "StandardError")] if log else []
    cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", f"--working-directory={cwd}"]
    try:
        done = run([*cmd, *env, *out, "--", *argv], capture_output=True, text=True, timeout=START_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DetachError(f"systemd-run: {exc}") from exc
    if done.returncode:
        raise DetachError(f"systemd-run exited {done.returncode}: {done.stderr.strip()[-300:]}")
