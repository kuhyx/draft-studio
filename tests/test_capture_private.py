"""Capture storage hygiene: 0700 profile and request dirs, and pruning of spent requests."""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from pathlib import Path

import pytest

from draft_studio.capture import login
from draft_studio.capture.pending import PendingStore
from draft_studio.capture.private import MAX_AGE_S, ensure_private, prune
from draft_studio.capture.service import CaptureService
from draft_studio.capture.spec import parse
from draft_studio.store import Store
from tests.test_capture_browser import FakeProc, Rig

SPEC = parse({"url": "https://x/", "name": "a", "steps": [{"click": "Zapłać"}]})


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_new_dir_is_0700_even_under_a_loose_umask(tmp_path: Path) -> None:
    old = os.umask(0)
    try:
        ensure_private(tmp_path / "a" / "profile")
    finally:
        os.umask(old)
    assert mode(tmp_path / "a" / "profile") == 0o700


def test_loose_dir_is_tightened_and_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    target = tmp_path / "profile"
    target.mkdir()
    target.chmod(0o755)
    with caplog.at_level(logging.WARNING, logger="draft_studio"):
        ensure_private(target)
    assert mode(target) == 0o700
    assert "0o755" in caplog.text and str(target) in caplog.text


def test_private_dir_is_left_alone_silently(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    target = tmp_path / "profile"
    target.mkdir(mode=0o700)
    with caplog.at_level(logging.DEBUG, logger="draft_studio"):
        ensure_private(target)
    assert mode(target) == 0o700 and caplog.text == ""


def test_headless_launch_enforces_the_profile_mode(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())
    rig.profile.mkdir()
    rig.profile.chmod(0o755)
    with rig.chromium.open():
        assert mode(rig.profile) == 0o700


def test_headless_launch_creates_the_profile_private(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())
    with rig.chromium.open():
        assert mode(rig.profile) == 0o700


def test_login_window_enforces_the_profile_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "chromium"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(binary))
    prof = Path(os.environ["DRAFT_STUDIO_DATA"]) / "capture-profile"
    prof.mkdir(parents=True)
    prof.chmod(0o750)

    class Window:
        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def popen(argv: list[str]) -> Window:
        assert mode(prof) == 0o700  # tightened before the window opens
        return Window()

    assert login.main(["login", "https://panel.example/"], popen=popen, watch=lambda *a: "closed") == 0
    assert mode(prof) == 0o700


def test_pending_dir_is_private_on_create(tmp_path: Path) -> None:
    pending = PendingStore(tmp_path)
    pending.create("d", SPEC, step=0, reason="deny-list", detail="x", blocked=[], grants=[], shot=b"png")
    assert mode(pending.dir) == 0o700
    pending.dir.chmod(0o755)
    pending.create("d", SPEC, step=0, reason="deny-list", detail="x", blocked=[], grants=[], shot=b"png")
    assert mode(pending.dir) == 0o700


def seed(pending: PendingStore, status: str, age_s: float = 0.0) -> str:
    rec = pending.create(
        "d", SPEC, step=0, reason="deny-list", detail="x", blocked=[], grants=[], shot=b"png"
    )
    rid = str(rec["id"])
    path = pending.dir / f"{rid}.json"
    rec.update(status=status, created=round(time.time() - age_s, 3))
    path.write_text(json.dumps(rec), encoding="utf-8")
    return rid


def left(pending: PendingStore) -> set[str]:
    return {p.name for p in pending.dir.iterdir()}


def test_prune_drops_denied_spent_and_old_records_with_their_shots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pending = PendingStore(tmp_path)
    fresh = seed(pending, "pending", 60)
    nearly = seed(pending, "pending", MAX_AGE_S - 3600)
    gone = [
        seed(pending, "denied"),
        seed(pending, "granted"),
        seed(pending, "pending", MAX_AGE_S + 3600),
    ]
    with caplog.at_level(logging.INFO, logger="draft_studio"):
        assert prune(pending.dir) == 3
    assert left(pending) == {f"{fresh}.json", f"{fresh}.png", f"{nearly}.json", f"{nearly}.png"}
    assert all(rid not in caplog.text for rid in gone) and "3" in caplog.text
    assert [r["id"] for r in pending.list_pending()] == [nearly, fresh]


def test_prune_keeps_a_granted_request_a_held_answer_still_waits_on(tmp_path: Path) -> None:
    """A re-run a restart cut is resumed from its record (heldjobs.py): the new server must not prune it."""
    pending = PendingStore(tmp_path)
    cut, spent = seed(pending, "granted"), seed(pending, "granted")
    assert prune(pending.dir, keep={cut}) == 1
    assert left(pending) == {f"{cut}.json", f"{cut}.png"} and spent not in "".join(left(pending))


def test_prune_removes_orphan_shots_and_temp_files_but_nothing_else(tmp_path: Path) -> None:
    pending = PendingStore(tmp_path)
    keep = seed(pending, "pending")
    (pending.dir / "deadbeefdeadbeef.png").write_bytes(b"orphan")
    (pending.dir / ".deadbeefdeadbeef.tmp").write_text("{}")
    (pending.dir / "notes.txt").write_text("mine")
    assert prune(pending.dir) == 0
    assert left(pending) == {f"{keep}.json", f"{keep}.png", "notes.txt"}


def test_prune_drops_unreadable_records(tmp_path: Path) -> None:
    pending = PendingStore(tmp_path)
    rid = "0123456789abcdef"
    pending.dir.mkdir()
    (pending.dir / f"{rid}.json").write_text("{not json")
    (pending.dir / f"{rid}.png").write_bytes(b"x")
    (pending.dir / "fedcba9876543210.json").write_text("[]")
    assert prune(pending.dir) == 2
    assert left(pending) == set()


def test_prune_without_a_dir_is_a_noop(tmp_path: Path) -> None:
    assert prune(tmp_path / "captures") == 0


def test_service_start_hardens_and_prunes(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    pending = PendingStore(store.root)
    keep = seed(pending, "pending")
    drop = seed(pending, "denied")
    pending.dir.chmod(0o755)
    (store.root / "capture-profile").mkdir()
    (store.root / "capture-profile").chmod(0o755)
    service = CaptureService(store)
    assert mode(store.root / "capture-profile") == 0o700 and mode(service.pending.dir) == 0o700
    assert [r["id"] for r in service.pending.list_pending()] == [keep]
    assert not (service.pending.dir / f"{drop}.png").exists()


def test_service_start_creates_both_dirs_private(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    service = CaptureService(store)
    assert mode(store.root / "capture-profile") == 0o700 and mode(service.pending.dir) == 0o700
