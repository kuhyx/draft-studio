"""The deterministic fake behind tests and the e2e, and the adapter picker."""

from __future__ import annotations

from pathlib import Path

import pytest

from draft_studio.claude import ClaudeError, CliClaude, Context
from draft_studio.claude_fake import FAKE_IMAGE, FakeClaude, make_claude
from tests.test_claude import ctx


def rw(f: FakeClaude, c: Context) -> str:
    return str(f.rewrite(c)["replacement"])


def test_fake_intake_rounds_and_lang() -> None:
    f = FakeClaude()
    first = f.intake(ctx(brief="Napisz maila do właściciela", lang=""))
    assert first["lang"] == "pl"
    assert len(first["questions"]) == 2
    done = f.intake(ctx(intake=[{"q": "a", "a": "b"}, {"q": "c", "a": "d"}]))
    assert done["questions"] == []
    two = ctx(brief="mail [2rounds]", intake=[{"q": "a", "a": "b"}, {"q": "c", "a": "d"}])
    assert len(f.intake(two)["questions"]) == 1
    assert f.intake(ctx(brief="x [noq]"))["questions"] == []
    assert f.intake(ctx(brief="hello", lang=""))["lang"] == "en"
    assert f.intake(ctx(brief="hello", lang="de"))["lang"] == "de"


def test_fake_draft_ask_rewrite() -> None:
    f = FakeClaude()
    d = f.draft(ctx(kind="mail", intake=[{"q": "Who?", "a": "Bob"}]))
    assert "Bob" in d["text"]
    assert d["subject"]
    assert f.draft(ctx())["subject"] == ""
    assert "[en]" in f.draft(ctx(kind="mail", lang=""))["subject"]
    assert f.ask(ctx(sel=(0, 5), instruction="why?", lang="pl")).startswith("[pl]")
    assert rw(f, ctx(sel=(0, 5))) == "Hello rewritten"
    assert rw(f, ctx(sel=(0, 5), instruction="shout")) == "HELLO"
    assert rw(f, ctx(text="one two three", sel=(0, 13))) == "one 2 three better"
    assert (
        rw(f, ctx(text="one two three", sel=(0, 13), instruction="lang", lang="pl")) == "[pl] one two three"
    )
    with pytest.raises(ClaudeError):
        f.ask(ctx(sel=(0, 5), instruction="[fail]"))
    with pytest.raises(ClaudeError):
        f.draft(ctx(brief="[fail]"))


def test_fake_invents_an_image_on_request() -> None:
    """[fakeimg] reproduces the incident: a link to an image nobody uploaded."""
    f = FakeClaude()
    assert rw(f, ctx(sel=(0, 5), instruction="[fakeimg]")) == f"Hello rewritten\n\n![](assets/{FAKE_IMAGE})"
    assert f.draft(ctx(brief="note [fakeimg]"))["text"].endswith(f"![](assets/{FAKE_IMAGE})")
    assert "assets/" not in f.draft(ctx())["text"]


def test_make_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE", "fake")
    assert isinstance(make_claude(), FakeClaude)
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE", "cli")
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE_BIN", "/x/claude")
    c = make_claude()
    assert isinstance(c, CliClaude)
    assert c.binary == Path("/x/claude")
    monkeypatch.delenv("DRAFT_STUDIO_CLAUDE_BIN")
    monkeypatch.setenv("HOME", "/home/u")
    c2 = make_claude()
    assert isinstance(c2, CliClaude)
    assert c2.binary == Path("/home/u/.local/bin/claude")
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE", "bogus")
    with pytest.raises(ValueError):
        make_claude()
