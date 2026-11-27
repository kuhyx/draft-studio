"""The first deploy on a clone whose web/dist is still a real directory, then a normal one.

deploy.sh runs for real (the pnpm build, not the prebuilt dist) from a copy of this repo state,
against a throwaway instance whose web/dist starts as a plain directory. Each deploy starts with
a fake ask in flight. A shim for `mv` and `rm` records whether web/dist resolves after every call:
the server must never have a missing dist, even for one instant. Run by hand:

    python3 -m pytest -q -m deploy tests/deploy_live/test_first_run_live.py
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.deploy_live.instance import REPO, Instance

pytestmark = pytest.mark.deploy

SHIM = '#!/bin/bash\n{real} "$@"\nrc=$?\n[[ -e "$DS_WEB/dist" ]] && s=present || s=missing\necho "$(basename "$0") $s" >> "{log}"\nexit $rc\n'


def copy_repo_state(dst: Path) -> Path:
    """The repo as it stands now (tracked and new files, edits included), without .git or node_modules."""
    cmd = ["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    for raw in subprocess.run(cmd, check=True, capture_output=True, timeout=20).stdout.split(b"\0"):
        src = REPO / raw.decode()
        if raw and src.is_file():
            (dst / raw.decode()).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / raw.decode())
    return dst


def make_shim(root: Path) -> tuple[Path, Path]:
    """A dir to put first on PATH: `mv` and `rm` that log whether web/dist is there after the call."""
    folder, log = root / "shim", root / "dist-state.log"
    folder.mkdir()
    for name in ("mv", "rm"):
        script = folder / name
        script.write_text(SHIM.format(real=shutil.which(name), log=log))
        script.chmod(0o755)
    return folder, log


def start_ask(instance: Instance, text: str) -> tuple[str, threading.Thread, list[Any]]:
    """A slow fake ask on a fresh draft, returned once the server reports it busy."""
    did = instance.post("/api/drafts", {"kind": "text"})["id"]
    instance.post(f"/api/drafts/{did}", {"text": text}, method="PUT")
    body = {"text": text, "from": 0, "to": 3, "question": "[slow] why"}
    answers: list[Any] = []

    def ask() -> None:  # the browser's request: cut when the old server goes
        try:
            answers.append(instance.post(f"/api/drafts/{did}/ask", body, timeout=40))
        except OSError as exc:
            answers.append(exc)

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.get("/api/busy")["busy"]:
        assert time.monotonic() < deadline, "the ask never became busy"
        time.sleep(0.05)
    return did, thread, answers


def assert_ask_lands_once(
    instance: Instance, did: str, thread: threading.Thread, answers: list[Any], text: str
) -> None:
    thread.join(40)
    assert isinstance(answers[0], OSError), "the old server's connection should have been cut"
    end, asks = time.monotonic() + 40, []
    while not asks and time.monotonic() < end:
        asks = instance.get(f"/api/drafts/{did}")["meta"]["asks"]
        time.sleep(0.2)
    assert [a["answer"] for a in asks] == [f"[en] About '{text[:3]}': [slow] why"]
    time.sleep(1.0)
    assert len(instance.get(f"/api/drafts/{did}")["meta"]["asks"]) == 1


def builds(instance: Instance) -> set[str]:
    return {p.name for p in instance.web.glob("dist.*")}


def test_first_run_converts_the_plain_dist_then_a_normal_run_prunes(plain_instance: Instance) -> None:
    web = plain_instance.web
    assert (web / "dist").is_dir() and not (web / "dist").is_symlink()
    repo = copy_repo_state(plain_instance.root / "repo")
    shim, log = make_shim(plain_instance.root)

    text1 = "one two three four"
    did, thread, answers = start_ask(plain_instance, text1)
    began = time.monotonic()
    first = plain_instance.deploy("c1", repo, real=True, shim=shim)
    took = time.monotonic() - began
    assert first.returncode == 0, first.stdout + first.stderr
    assert took < 60, f"the whole deploy took {took:.1f}s"
    assert plain_instance.get("/version")["build"] == "c1"
    assert (web / "dist").is_symlink() and (web / "dist").readlink() == Path("dist.c1")
    assert (web / "dist.legacy" / "build-id").read_text().strip() == "b1"
    assert builds(plain_instance) == {"dist.c1", "dist.legacy"}
    assert_ask_lands_once(plain_instance, did, thread, answers, text1)

    text2 = "five six seven eight"
    did, thread, answers = start_ask(plain_instance, text2)
    second = plain_instance.deploy("c2", repo, real=True, shim=shim)
    assert second.returncode == 0, second.stdout + second.stderr
    assert plain_instance.get("/version")["build"] == "c2"
    assert (web / "dist").readlink() == Path("dist.c2")
    assert builds(plain_instance) == {"dist.c2", "dist.c1"}, "keep the last 2; dist.legacy is an old build"
    assert_ask_lands_once(plain_instance, did, thread, answers, text2)

    states = log.read_text().split("\n")
    assert "mv present" in states, "the shim saw no swap at all"
    assert "missing" not in " ".join(states), "web/dist was missing at some instant:\n" + log.read_text()


def test_redeploying_the_live_build_never_removes_it(instance: Instance) -> None:
    shim, log = make_shim(instance.root)
    assert instance.deploy("b2", shim=shim).returncode == 0
    again = instance.deploy("b2", shim=shim)
    assert again.returncode == 0, again.stdout + again.stderr
    assert instance.get("/version")["build"] == "b2"
    assert (instance.web / "dist" / "build-id").read_text().strip() == "b2"
    assert "missing" not in log.read_text(), (
        "the live build dir was removed while it was served:\n" + log.read_text()
    )
