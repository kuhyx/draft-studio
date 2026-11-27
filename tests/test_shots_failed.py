"""A proposal whose screenshots were all dropped is held failed; some dropped lands partial; old images stay.

kuhy (2026-11-27): "it suggested edits but the images stayed the same". Four captures failed with
no-target and the answer was released as a text-only proposal that also cut the two images the span held.
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.claude_fake import FakeClaude
from draft_studio.shotreq import marker
from tests.conftest import App
from tests.test_capture_service import ShotPage, use
from tests.test_held import SAVE, grant, held, hold, saving
from tests.test_shots import STEP, URL, failed_hold, propose, text_draft

OLD = "![Twoje usługi](assets/krok3-old.png)"
GUIDE = f"{STEP}\n\n{OLD}"


def shot(key: str, text: str) -> dict[str, Any]:
    """A standalone capture: click `text` on the page, frame it."""
    aim = {"text": text, "role": "", "within": "", "nth": 0}
    return {
        "key": key,
        "walk": "",
        "fresh": False,
        "url": URL,
        "steps": [{"kind": "click", **aim}],
        "highlight": aim,
        "alt": key.title(),
    }


def reply(*keys: tuple[str, str], body: str = "Krok.") -> Any:
    """Claude's answer: the step text, then each shot's marker line; `keys` = (key, text to click)."""
    text = "\n\n".join([body, *(marker(k) for k, _ in keys)])
    captures = [shot(k, t) for k, t in keys]
    return lambda self, ctx: {"replacement": text, "captures": captures}


def test_all_dropped_is_held_failed_with_its_reasons_and_nothing_lands(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(app, lambda: ShotPage(["Zapisz"]))
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Nic"), ("b", "Nigdzie")))
    did = text_draft(app, GUIDE)
    status, body = propose(app, did, GUIDE)
    assert failed_hold(app, did, status, body) == [
        {"key": "a", "reason": "no-target"},
        {"key": "b", "reason": "no-target"},
    ]
    listing = held(app, did)
    assert [h["failed"] for h in listing["held"]] == [body["held"]["failed"]] and listing["released"] == []
    assert app.store.get(did)["text"] == GUIDE and app.store.list_assets(did) == []
    assert [e for e in app.store.history(did) if e["type"] == "proposal"] == []


def test_some_dropped_lands_partial_and_the_proposal_names_the_missing_shot(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(app, lambda: ShotPage(["Zapisz"]))
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Zapisz"), ("b", "Nic")))
    did = text_draft(app, GUIDE)
    status, prop = propose(app, did, GUIDE)
    assert status == 200 and prop["partial"] is True and prop["outcome"] == "ok", prop
    assert prop["dropped"] == [{"key": "b", "reason": "no-target"}]
    assert "![A](assets/a-" in prop["replacement"]
    (pending,) = app.store.get(did)["proposals"]
    assert pending["missing"] == ["b"]


def test_a_complete_answer_is_not_partial_and_carries_no_missing(app: App) -> None:
    use(app, lambda: ShotPage(["Pokaż szczegóły"]))
    did = text_draft(app, STEP)
    status, prop = propose(app, did, STEP)
    assert status == 200 and prop["partial"] is False
    assert "missing" not in app.store.get(did)["proposals"][0]


def test_the_old_image_stays_where_its_new_shot_is_missing(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    use(app, lambda: ShotPage(["Zapisz"]))
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Zapisz"), ("b", "Nic")))
    old_two = "![Domeny](assets/krok4-old.png)"
    guide = f"{STEP}\n\n{OLD}\n\nKrok dwa.\n\n{old_two}"
    did = text_draft(app, guide)
    status, prop = propose(app, did, guide)
    out = prop["replacement"]
    assert status == 200 and "![A](assets/a-" in out, prop
    assert OLD not in out  # its step got a new shot
    assert old_two in out  # its step lost the new shot: the old one stays
    assert (prop["stripped"], app.store.get(did)["proposals"][0]["missing"]) == ([], ["b"])


def test_a_text_only_rewrite_keeps_every_image_of_the_span(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeClaude, "rewrite", lambda self, ctx: {"replacement": "Krótko.", "captures": []})
    did = text_draft(app, GUIDE)
    status, prop = propose(app, did, GUIDE)
    assert status == 200 and prop["replacement"] == f"Krótko.\n\n{OLD}" and prop["stripped"] == []


def test_the_instruction_may_ask_to_remove_the_images(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeClaude, "rewrite", lambda self, ctx: {"replacement": "Krótko.", "captures": []})
    did = text_draft(app, GUIDE)
    status, prop = propose(app, did, GUIDE, "usuń zrzuty ekranu")
    assert status == 200 and prop["replacement"] == "Krótko."


def test_an_answer_without_captures_releases_as_before(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        FakeClaude, "rewrite", lambda self, ctx: {"replacement": "Nowy tekst", "captures": []}
    )
    did = text_draft(app, "Stary tekst")
    status, prop = propose(app, did, "Stary tekst", "x")
    assert (status, prop["replacement"], prop["dropped"], prop["partial"]) == (200, "Nowy tekst", [], False)


def test_retry_runs_a_failed_hold_again_and_releases_when_it_works(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = use(app, lambda: ShotPage(["Zapisz"]))
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Nic")))
    did = text_draft(app, GUIDE)
    status, body = propose(app, did, GUIDE)
    hid = body["held"]["id"]
    assert failed_hold(app, did, status, body) and body["held"]["tries"] == 0

    status, listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": hid})
    assert status == 200 and listing["released"] == []
    (again,) = listing["held"]
    assert again["tries"] == 1 and again["failed"] == [{"key": "a", "reason": "no-target"}]

    launcher.make = lambda: ShotPage(["Nic"])
    listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": hid})[1]
    assert listing["held"] == []
    (out,) = listing["released"]
    assert "![A](assets/a-" in out["replacement"] and out["dropped"] == [] and out["partial"] is False


def test_retry_of_a_failed_walk_runs_it_once_for_all_its_shots(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = use(app, lambda: ShotPage([]))
    captures = [{**shot(k, "Nic"), "walk": "w"} for k in ("a", "b")]
    text = "\n\n".join(["Krok.", marker("a"), "Dalej.", marker("b")])
    monkeypatch.setattr(FakeClaude, "rewrite", lambda self, ctx: {"replacement": text, "captures": captures})
    did = text_draft(app, GUIDE)
    status, body = propose(app, did, GUIDE)
    failed_hold(app, did, status, body)
    runs = launcher.runs
    app.call("POST", f"/api/drafts/{did}/held", {"retry": body["held"]["id"]})
    assert launcher.runs == runs + 3  # one walk run and its two fixes, not one run per shot


def test_dismissing_a_failed_hold_forgets_it_and_only_it(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    saving(app)
    did, waiting = hold(app)  # a hold still waiting on kuhy's permission
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Nic")))
    status, body = propose(app, did, SAVE)
    assert failed_hold(app, did, status, body)
    ids = [body["held"]["id"], waiting["id"]]
    assert app.call("POST", f"/api/drafts/{did}/held", {"collect": ids}) == (200, {"collected": 1})
    (left,) = held(app, did)["held"]
    assert left["id"] == waiting["id"] and left["failed"] == []


def test_a_denied_hold_with_a_failed_sibling_shot_lands_partial(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    saving(app)
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Zapisz"), ("b", "Nic")))
    did = text_draft(app, SAVE)
    status, body = propose(app, did, SAVE)
    assert status == 202 and body["held"]["failed"] == [], body  # waits: one shot is still on kuhy
    assert grant(app, body["held"]["requests"][0]["id"])[0] == 200
    (out,) = held(app, did)["released"]
    assert out["partial"] is True and out["dropped"] == [{"key": "b", "reason": "no-target"}]
    assert app.store.get(did)["proposals"][0]["missing"] == ["b"]


def test_a_first_draft_with_every_shot_dropped_is_still_delivered(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(app, lambda: ShotPage(["Zapisz"]))
    text = f"Witaj.\n\n{marker('a')}"
    draft = {"text": text, "subject": "", "captures": [shot("a", "Nic")]}
    monkeypatch.setattr(FakeClaude, "draft", lambda self, ctx: draft)
    did = str(app.store.create("text")["id"])
    status, step = app.call("POST", f"/api/drafts/{did}/brief", {"brief": "[noq] x"})
    assert status == 200 and step["state"]["text"] == "Witaj." and step["partial"] is True


def test_a_walk_shot_taken_but_not_in_the_text_does_not_save_the_answer(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(app, lambda: ShotPage([]))
    captures = [{**shot(k, "Nic"), "walk": "w"} for k in ("a", "b")]
    text = f"Krok.\n\n{marker('b')}"  # only b is placed
    monkeypatch.setattr(FakeClaude, "rewrite", lambda self, ctx: {"replacement": text, "captures": captures})
    ran = (200, {"shots": [{"name": "a", "asset": "a-1.png"}]})  # the walk ended before b's shot
    monkeypatch.setattr(app.httpd.captures, "capture", lambda *a, **k: ran)
    did = text_draft(app, GUIDE)
    status, body = propose(app, did, GUIDE)
    assert failed_hold(app, did, status, body) == [{"key": "b", "reason": "not-shot"}]
    listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": body["held"]["id"]})[1]
    assert listing["released"] == [] and listing["held"][0]["tries"] == 1
