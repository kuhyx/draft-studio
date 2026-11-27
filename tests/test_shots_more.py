"""Edges of the screenshot pipeline: several captures in one answer, fixes in a first draft, the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from draft_studio import claude, shotout
from draft_studio.claude import ClaudeError, CliClaude, Context
from draft_studio.claude_fake import FakeClaude, shot_request
from draft_studio.held import HeldStore
from draft_studio.links import LinkError
from draft_studio.shotreq import marker
from tests.conftest import App
from tests.test_capture_service import ShotPage, use
from tests.test_claude import Recorder, ctx, result
from tests.test_held import EFFECTS, grant, held
from tests.test_shots import URL, propose, text_draft

TWO = f"Otworzyć {URL}: „Pokaż szczegóły” i „Zapisz”."


def two_shots(self: FakeClaude, c: Context) -> dict[str, Any]:
    one = {
        "key": "a",
        "url": URL,
        "steps": [{"kind": "click", "text": "Pokaż szczegóły"}],
        "highlight": "",
        "alt": "A",
    }
    two = {**one, "key": "b", "steps": [{"kind": "click", "text": "Zapisz"}], "alt": "B"}
    return {"replacement": f"x\n\n{marker('a')}\n\n{marker('b')}", "captures": [one, two]}


def test_one_answer_with_a_resolved_and_a_held_capture(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    use(app, lambda: ShotPage(["Pokaż szczegóły", "Zapisz"], EFFECTS))
    monkeypatch.setattr(FakeClaude, "rewrite", two_shots)
    did = text_draft(app, TWO)
    other = text_draft(app, TWO)
    _, first = propose(app, other, TWO)  # another held answer the request lookup must skip
    status, body = propose(app, did, TWO)
    assert status == 202
    rid = body["held"]["requests"][0]["id"]
    assert HeldStore(app.store).by_request("f" * 16) is None  # scans every slot of every held answer
    _, listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": body["held"]["id"]})
    assert [h["id"] for h in listing["held"]] == [body["held"]["id"]]  # the pending one is not retried
    assert app.call("POST", f"/api/drafts/{did}/held", {"collect": [body["held"]["id"]]}) == (
        200,
        {"collected": 0},
    )
    assert grant(app, rid)[0] == 200
    out = held(app, did)["released"][0]["replacement"]
    assert out.startswith("x\n\n![A](assets/") and "![B](assets/" in out
    assert [h["id"] for h in held(app, other)["held"]] == [first["held"]["id"]]


def test_a_first_draft_capture_is_fixed_in_the_draft_context(app: App) -> None:
    use(app, lambda: ShotPage(["Pokaż szczegóły"]))
    did = str(app.store.create("text")["id"])
    brief = f"[noq] [shot] Otworzyć {URL} i kliknąć „pokaz SZCZEGOLY”."
    status, step = app.call("POST", f"/api/drafts/{did}/brief", {"brief": brief})
    assert status == 200 and "](assets/" in step["state"]["text"]


def test_release_falls_back_to_no_images_if_the_last_gate_fails(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(app, lambda: ShotPage(["Pokaż szczegóły"]))
    real = shotout.strip_invented
    calls: list[str] = []

    def gate(text: str, stored: Any, kuhy: str = "") -> tuple[str, list[str]]:
        calls.append(text)
        if len(calls) == 2:
            raise LinkError("x")
        return real(text, stored, kuhy)

    monkeypatch.setattr(shotout, "strip_invented", gate)
    text = f"Otworzyć {URL} i kliknąć „Pokaż szczegóły”."
    status, prop = propose(app, text_draft(app, text), text)
    assert status == 200 and prop["replacement"] == text


def test_fake_requests_nothing_without_a_url() -> None:
    assert shot_request("no link „A”", "nor here") == []
    assert shot_request("„A”", "see https://x.example/.")[0]["url"] == "https://x.example/"
    assert shot_request("https://x.example", "")[0]["highlight"] == ""
    with pytest.raises(ClaudeError):
        FakeClaude().fix_capture(ctx(instruction="[fail]"), {}, {})


def test_cli_captures_and_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    c = CliClaude(binary=Path("/bin/claude"))
    cap = {"key": "a", "url": URL, "steps": [], "highlight": "", "alt": ""}
    rec = Recorder(result({"replacement": "r", "captures": [cap]}))
    monkeypatch.setattr(claude.subprocess, "run", rec)
    assert c.rewrite(ctx(sel=(0, 5))) == {"replacement": "r", "captures": [cap]}
    schema = rec.argv[rec.argv.index("--json-schema") + 1]
    assert '"captures"' in schema and "[[[IMG:<key>]]]" in rec.argv[rec.argv.index("--system-prompt") + 1]
    monkeypatch.setattr(claude.subprocess, "run", Recorder(result({"replacement": "r", "captures": "x"})))
    with pytest.raises(ClaudeError):
        c.rewrite(ctx(sel=(0, 5)))
    rec = Recorder(result({"capture": cap}))
    monkeypatch.setattr(claude.subprocess, "run", rec)
    error = {"error": "no-target", "text": "X", "seen": ["Y"]}
    assert c.fix_capture(ctx(sel=(0, 5)), cap, error) == cap
    prompt = rec.argv[rec.argv.index("-p") + 1]
    assert '"seen": ["Y"]' in prompt and "same key and url" in prompt
    monkeypatch.setattr(claude.subprocess, "run", Recorder(result({"capture": 5})))
    with pytest.raises(ClaudeError):
        c.fix_capture(ctx(sel=(0, 5)), cap, error)
