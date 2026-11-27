"""Claude's own screenshots, resolved before the answer reaches kuhy (fake browser, fake Claude)."""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.claude import Context
from draft_studio.claude_fake import FakeClaude
from draft_studio.shotreq import marker
from tests.capture_fakes import FakePage
from tests.conftest import App
from tests.test_capture_service import ShotPage, use

URL = "https://panel.example/"
STEP = f"Otworzyć {URL} i kliknąć „Pokaż szczegóły”."


def text_draft(app: App, text: str, brief: str = "Guide") -> str:
    did = str(app.store.create("text")["id"])
    app.store.save(did, {"text": text, "brief": brief, "phase": "editing"})
    return did


def propose(app: App, did: str, needle: str, instruction: str = "[shot]") -> tuple[int, Any]:
    text = app.store.get(did)["text"]
    start = text.index(needle)
    body = {"text": text, "from": start, "to": start + len(needle), "instruction": instruction}
    return app.call("POST", f"/api/drafts/{did}/propose", body)


def test_proposal_arrives_with_a_real_stored_image(app: App) -> None:
    launcher = use(app, lambda: ShotPage(["Pokaż szczegóły"]))
    did = text_draft(app, STEP)
    status, prop = propose(app, did, STEP)
    assert status == 200, prop
    (asset,) = [a["name"] for a in app.store.list_assets(did)]
    assert prop["replacement"] == f"{STEP}\n\n![Pokaż szczegóły](assets/{asset})"
    assert (prop["stripped"], prop["dropped"]) == ([], [])
    assert launcher.runs == 1
    logged = [e for e in app.store.history(did) if e["type"] == "proposal"]
    assert logged[-1]["replacement"] == prop["replacement"]


def test_a_wrong_click_text_is_fixed_from_seen(app: App) -> None:
    use(app, lambda: ShotPage(["Pokaż szczegóły", "Zapisz"]))
    text = f"Otworzyć {URL} i kliknąć „Pokaz szczegoly”."
    status, prop = propose(app, text_draft(app, text), text)
    assert status == 200 and "](assets/" in prop["replacement"], prop


def test_fixes_are_capped_then_the_line_goes(app: App) -> None:
    launcher = use(app, lambda: ShotPage(["Zapisz"]))
    text = f"Otworzyć {URL} i kliknąć „Nic”."
    did = text_draft(app, text)
    status, prop = propose(app, did, text)
    assert status == 200
    assert "IMG" not in prop["replacement"] and "assets/" not in prop["replacement"]
    assert prop["dropped"] == [{"key": "shot-1", "reason": "no-target"}]
    assert launcher.runs == 3  # the first try and two fixes
    assert app.store.list_assets(did) == []


def test_an_invented_url_never_opens_the_browser(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = use(app, lambda: ShotPage(["Pokaż szczegóły"]))
    real = FakeClaude.rewrite

    def invent(self: FakeClaude, ctx: Context) -> dict[str, Any]:
        out = real(self, ctx)
        out["captures"][0]["url"] = "https://panel.example/invented"
        return out

    monkeypatch.setattr(FakeClaude, "rewrite", invent)
    did = text_draft(app, STEP)
    status, prop = propose(app, did, STEP)
    assert status == 200 and prop["dropped"] == [{"key": "shot-1", "reason": "url"}]
    assert prop["replacement"] == STEP and launcher.runs == 0


def test_markers_without_requests_and_bad_requests_go(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    use(app, lambda: ShotPage([]))
    bad = {"key": "bad", "url": URL, "steps": "x", "highlight": "", "alt": ""}
    reply = {"replacement": f"A\n\n{marker('ghost')}\n\n{marker('bad')}\n\nB", "captures": [bad]}
    monkeypatch.setattr(FakeClaude, "rewrite", lambda self, ctx: reply)
    did = text_draft(app, "A B")
    status, prop = propose(app, did, "A B", "x")
    assert status == 200 and prop["replacement"] == "A\n\nB"
    assert prop["dropped"] == [{"key": "ghost", "reason": "no-request"}, {"key": "bad", "reason": "bad-spec"}]


@pytest.mark.parametrize(
    ("page_texts", "reason"),
    [(["Pokaż szczegóły", "Pokaż szczegóły"], "ambiguous-target"), ([], "no-target")],
)
def test_unfixable_targets_drop(app: App, page_texts: list[str], reason: str) -> None:
    use(app, lambda: ShotPage(page_texts))
    did = text_draft(app, STEP)
    assert propose(app, did, STEP)[1]["dropped"] == [{"key": "shot-1", "reason": reason}]


def test_browser_failures_drop_the_line(app: App) -> None:
    use(app, lambda: FakePage(["Pokaż szczegóły"]))  # not a decodable PNG: bad-image
    did = text_draft(app, STEP)
    status, prop = propose(app, did, STEP)
    assert status == 200 and prop["dropped"] == [{"key": "shot-1", "reason": "bad-image"}]
    assert prop["replacement"] == STEP


def test_claude_failing_to_fix_drops_the_line(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    use(app, lambda: ShotPage(["Zapisz"]))

    def broken(self: FakeClaude, ctx: Context, capture: Any, error: Any) -> dict[str, Any]:
        return {"key": "x", "url": URL, "steps": "bad", "highlight": "", "alt": ""}

    monkeypatch.setattr(FakeClaude, "fix_capture", broken)
    did = text_draft(app, STEP)
    assert propose(app, did, STEP)[1]["dropped"] == [{"key": "shot-1", "reason": "no-target"}]


def test_first_draft_takes_its_screenshot(app: App) -> None:
    use(app, lambda: ShotPage(["Pokaż szczegóły"]))
    did = str(app.store.create("text")["id"])
    status, step = app.call("POST", f"/api/drafts/{did}/brief", {"brief": f"[noq] [shot] {STEP}"})
    assert status == 200, step
    text = step["state"]["text"]
    assert "](assets/" in text and "IMG" not in text
    assert step["state"]["meta"]["phase"] == "editing" and step["dropped"] == []


def test_first_draft_records_dropped_and_stripped(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    use(app, lambda: ShotPage([]))
    text = f"Hi ![](assets/nope.png)\n\n{marker('ghost')}"
    monkeypatch.setattr(FakeClaude, "draft", lambda self, ctx: {"text": text, "subject": "", "captures": []})
    did = str(app.store.create("text")["id"])
    status, step = app.call("POST", f"/api/drafts/{did}/brief", {"brief": "[noq] note"})
    assert status == 200 and step["state"]["text"] == "Hi"
    kinds = [e["type"] for e in app.store.history(did)]
    assert "stripped" in kinds and "dropped" in kinds
