"""Revising a pending proposal: kuhy comments on Claude's replacement before accepting it."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio import claude
from draft_studio.claude import ClaudeError, CliClaude, Context
from draft_studio.claude_fake import FakeClaude
from tests.conftest import App

TEXT = "alpha beta gamma end"
OLD = "alpha 2 gamma better"


def ctx(**kw: Any) -> Context:
    base: dict[str, Any] = {"kind": "text", "lang": "en", "brief": "A note", "intake": [], "text": TEXT}
    base.update(sel=(0, 16), **kw)
    return Context(**base)


def test_prompt_carries_the_old_replacement_only_when_revising() -> None:
    out = ctx(previous=OLD, instruction="shorter").render()
    assert f"Your pending replacement for the marked span:\n{OLD}" in out
    assert out.index(OLD) < out.index("Instruction:\nshorter")
    assert "pending replacement" not in ctx(instruction="shorter").render()


def test_cli_asks_for_a_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        prompts.append(argv[argv.index("-p") + 1])
        out = '[{"type": "result", "is_error": false, "structured_output": {"replacement": "r"}}]'
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(claude.subprocess, "run", run)
    c = CliClaude(binary=Path("/bin/claude"))
    assert c.rewrite(ctx(previous=OLD, instruction="shorter")) == "r"
    assert c.rewrite(ctx(previous=OLD)) == "r"
    assert c.rewrite(ctx(instruction="shorter")) == "r"
    assert c.rewrite(ctx()) == "r"
    revise, plain, change, fresh = (p.split("\n\n", 1)[0] for p in prompts)
    assert "Revise your pending replacement" in revise and "comment" in revise
    assert "Revise your pending replacement" in plain and "read better" in plain
    assert "Revise" not in change and "instruction" in change
    assert "Revise" not in fresh and "pending" not in fresh


def test_fake_revises_the_old_replacement() -> None:
    f = FakeClaude()
    assert f.rewrite(ctx(previous=OLD, instruction="shorter")) == "alpha 2 gamma"
    assert f.rewrite(ctx(previous="solo")) == "solo revised"
    with pytest.raises(ClaudeError):
        f.rewrite(ctx(previous=OLD, instruction="[fail]"))


def test_propose_with_previous(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    app.call("PUT", f"/api/drafts/{did}", {"text": TEXT, "claude": []})
    body = {"text": TEXT, "from": 0, "to": 16, "instruction": "shorter", "previous": OLD}
    status, prop = app.call("POST", f"/api/drafts/{did}/propose", body)
    assert status == 200
    assert prop == {"replacement": "alpha 2 gamma", "from": 0, "to": 16, "stripped": []}
    _, hist = app.call("GET", f"/api/drafts/{did}/history")
    logged = [h for h in hist["versions"] if h["type"] == "proposal"]
    assert logged[-1]["previous"] == OLD and logged[-1]["instruction"] == "shorter"
    assert app.call("POST", f"/api/drafts/{did}/propose", {**body, "previous": 5}) == (
        400,
        {"error": "bad-previous"},
    )


def test_revision_keeps_the_links_of_the_old_replacement(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    app.call("PUT", f"/api/drafts/{did}", {"text": TEXT, "claude": []})
    # The fake keeps all but the last word, so the revision still links what `previous` linked.
    body = {"text": TEXT, "from": 0, "to": 5, "previous": "alpha ![](assets/nope.png) tail"}
    status, prop = app.call("POST", f"/api/drafts/{did}/propose", body)
    assert status == 200
    assert prop["replacement"] == "alpha ![](assets/nope.png)" and prop["stripped"] == []


def test_revision_adding_an_invented_image_is_stripped(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeClaude, "rewrite", lambda self, c: f"{c.previous}\n![](https://e.com/new.png)")
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    app.call("PUT", f"/api/drafts/{did}", {"text": TEXT, "claude": []})
    body = {"text": TEXT, "from": 0, "to": 5, "previous": OLD}
    status, prop = app.call("POST", f"/api/drafts/{did}/propose", body)
    assert status == 200
    assert prop["replacement"] == OLD and prop["stripped"] == ["![](https://e.com/new.png)"]
