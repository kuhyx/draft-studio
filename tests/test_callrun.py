"""draft_studio.callrun: the detached process that runs one journaled Claude call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from draft_studio.callio import ctx_from_json, ctx_to_json

from draft_studio import callrun
from draft_studio.claude import Claude, ClaudeError, Context
from draft_studio.claude_fake import FakeClaude

CTX = Context(kind="text", lang="en", brief="b", intake=[], text="one two three four", sel=(0, 3))


def call_dir(tmp_path: Path, method: str, **extra: Any) -> Path:
    cdir = tmp_path / "call-0"
    cdir.mkdir()
    (cdir / "call.json").write_text(json.dumps({"method": method, "ctx": ctx_to_json(CTX), **extra}))
    return cdir


def result(cdir: Path) -> dict[str, Any]:
    return json.loads((cdir / "result.json").read_text())


def test_runs_the_method_and_writes_pid_and_result(tmp_path: Path) -> None:
    cdir = call_dir(tmp_path, "ask")
    assert callrun.main([str(cdir)], factory=FakeClaude) == 0
    assert result(cdir) == {"ok": True, "value": "[en] About 'one': "}
    assert (cdir / "pid").read_text().split()[0].isdigit()


def test_fix_capture_gets_its_extra_arguments(tmp_path: Path) -> None:
    cap = {"steps": [{"text": "Zapisz"}], "highlight": {"text": "Zapisz"}}
    cdir = call_dir(tmp_path, "fix_capture", capture=cap, error={"text": "zapisz", "seen": ["Zapisz"]})
    callrun.main([str(cdir)], factory=FakeClaude)
    assert result(cdir)["value"]["steps"] == [{"text": "Zapisz"}]


def test_a_claude_failure_is_a_result_not_a_crash(tmp_path: Path) -> None:
    class Broken(FakeClaude):
        def ask(self, ctx: Context) -> str:
            raise ClaudeError("no answer")

    cdir = call_dir(tmp_path, "ask")
    assert callrun.main([str(cdir)], factory=Broken) == 0
    assert result(cdir) == {"ok": False, "error": "no answer"}


def test_any_other_failure_is_named_in_the_result(tmp_path: Path) -> None:
    def factory() -> Claude:
        raise KeyError("HOME")

    cdir = call_dir(tmp_path, "ask")
    assert callrun.main([str(cdir)], factory=factory) == 0
    assert "KeyError" in result(cdir)["error"]


def test_a_method_off_the_list_is_refused(tmp_path: Path) -> None:
    cdir = call_dir(tmp_path, "__init__")
    callrun.main([str(cdir)], factory=FakeClaude)
    assert result(cdir)["ok"] is False


def test_context_round_trips() -> None:
    ctx = Context(
        kind="mail",
        lang="pl",
        brief="b",
        intake=[{"q": "q", "a": "a"}],
        text="t",
        sel=(1, 2),
        assets=("a.png",),
    )
    assert ctx_from_json(json.loads(json.dumps(ctx_to_json(ctx)))) == ctx
    plain = Context(kind="text", lang="", brief="", intake=[], text="")
    assert ctx_from_json(ctx_to_json(plain)) == plain


def test_slow_marker_stalls_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setenv("DRAFT_STUDIO_FAKE_SLOW", "7")
    monkeypatch.setattr("draft_studio.claude_fake.time.sleep", slept.append)
    ctx = Context(
        kind="text", lang="en", brief="b", intake=[], text="abc", sel=(0, 1), instruction="[slow] hi"
    )
    FakeClaude().ask(ctx)
    assert slept == [7.0]
