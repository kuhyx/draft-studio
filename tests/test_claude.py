"""Claude adapter: prompt context, CLI argv and parsing, fake determinism, errors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio import claude
from draft_studio.claude import (
    ClaudeError,
    CliClaude,
    Context,
    FakeClaude,
    make_claude,
    mark_span,
)


def ctx(**kw: Any) -> Context:
    base: dict[str, Any] = {
        "kind": "text",
        "lang": "en",
        "brief": "A note",
        "intake": [],
        "text": "Hello world",
    }
    base.update(kw)
    return Context(**base)


def test_mark_span() -> None:
    assert mark_span("Hello world", 6, 11) == "Hello [[[SEL]]]world[[[/SEL]]]"
    with pytest.raises(ValueError):
        mark_span("abc", 2, 1)
    with pytest.raises(ValueError):
        mark_span("abc", 0, 9)


def test_context_render_has_everything() -> None:
    c = ctx(intake=[{"q": "Who?", "a": "Bob"}], kind="mail", subject="Hi", sel=(0, 5), instruction="shorter")
    out = c.render()
    for part in ("A note", "Who?", "Bob", "[[[SEL]]]Hello[[[/SEL]]] world", "shorter", "mail", "Hi", "en"):
        assert part in out
    assert "(none yet)" in ctx(text="").render()


class Recorder:
    def __init__(self, payload: Any, code: int = 0) -> None:
        self.payload = payload
        self.code = code
        self.argv: list[str] = []

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.argv = argv
        self.kw = kw
        out = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return subprocess.CompletedProcess(argv, self.code, out, "boom")


def result(obj: Any) -> list[dict[str, Any]]:
    return [{"type": "system"}, {"type": "result", "is_error": False, "structured_output": obj}]


def test_cli_intake_argv_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(result({"lang": "pl", "questions": ["Kto?"]}))
    monkeypatch.setattr(claude.subprocess, "run", rec)
    out = CliClaude(binary=Path("/bin/claude"), model="sonnet").intake(ctx())
    assert out == {"lang": "pl", "questions": ["Kto?"]}
    argv = rec.argv
    assert argv[0] == "/bin/claude"
    for flag in ("-p", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--json-schema"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--bare" not in argv  # --bare forces an API key; subscription only
    assert rec.kw["timeout"] == 300


def test_cli_draft_ask_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    c = CliClaude(binary=Path("/bin/claude"))
    monkeypatch.setattr(claude.subprocess, "run", Recorder(result({"text": "Body", "subject": "S"})))
    assert c.draft(ctx()) == {"text": "Body", "subject": "S"}
    monkeypatch.setattr(claude.subprocess, "run", Recorder(result({"answer": "Because."})))
    assert c.ask(ctx(sel=(0, 5), instruction="why?")) == "Because."
    monkeypatch.setattr(claude.subprocess, "run", Recorder(result({"replacement": "Hi"})))
    assert c.rewrite(ctx(sel=(0, 5))) == "Hi"


@pytest.mark.parametrize(
    "payload,code",
    [
        ("not json", 0),
        ([{"type": "result", "is_error": True, "result": "x"}], 0),
        ([{"type": "system"}], 0),
        (result({"wrong": 1}), 0),
        (result({"answer": 5}), 0),
        (result({"lang": "en", "questions": [1]}), 0),
        (result("str"), 0),
        ({"type": "result"}, 0),
        (result({"answer": "x"}), 1),
    ],
)
def test_cli_errors(monkeypatch: pytest.MonkeyPatch, payload: Any, code: int) -> None:
    monkeypatch.setattr(claude.subprocess, "run", Recorder(payload, code))
    c = CliClaude(binary=Path("/bin/claude"))
    with pytest.raises(ClaudeError):
        if isinstance(payload, list) and payload and "lang" in str(payload):
            c.intake(ctx())
        else:
            c.ask(ctx(sel=(0, 1), instruction="q"))


def test_cli_timeout_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(argv: list[str], **kw: Any) -> None:
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(claude.subprocess, "run", slow)
    with pytest.raises(ClaudeError):
        CliClaude(binary=Path("/bin/claude")).ask(ctx(sel=(0, 1), instruction="q"))

    def missing(argv: list[str], **kw: Any) -> None:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(claude.subprocess, "run", missing)
    with pytest.raises(ClaudeError):
        CliClaude(binary=Path("/nope")).ask(ctx(sel=(0, 1), instruction="q"))


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
    assert f.rewrite(ctx(sel=(0, 5))) == "Hello rewritten"
    assert f.rewrite(ctx(sel=(0, 5), instruction="shout")) == "HELLO"
    assert f.rewrite(ctx(text="one two three", sel=(0, 13))) == "one 2 three better"
    assert (
        f.rewrite(ctx(text="one two three", sel=(0, 13), instruction="lang", lang="pl"))
        == "[pl] one two three"
    )
    with pytest.raises(ClaudeError):
        f.ask(ctx(sel=(0, 5), instruction="[fail]"))
    with pytest.raises(ClaudeError):
        f.draft(ctx(brief="[fail]"))


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
