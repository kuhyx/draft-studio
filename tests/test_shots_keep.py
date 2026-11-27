"""A walk that fails at step N keeps the shots it took before step N (kuhy, 2026-11-27).

Live: a 4-shot walk (login page, then 3 shots after clicks) failed `no-target` at its
2nd click and every shot was marked failed, the login-page shot taken fine included.
Now the 422 carries the shots taken so far (capture/service.py), the slots keep them
(shots.py), and the answer follows the usual rules: some missing lands partial.
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.assets import AssetError
from draft_studio.capture import errors
from draft_studio.capture.runner import run_all
from draft_studio.capture.spec import parse
from draft_studio.claude import Context
from draft_studio.claude_fake import FakeClaude
from tests.capture_fakes import FakePage
from tests.conftest import App
from tests.test_capture_service import draft, use
from tests.test_capture_walk_service import Panel, spec
from tests.test_held import grant, held
from tests.test_shots import failed_hold
from tests.test_shots_walk import aim, click, images, start, walk

KEPT = ["login", "dashboard"]  # taken before the walk's 2nd click (step 3 of its spec)


def broken_walk() -> list[dict[str, Any]]:
    """kuhy's walk whose 2nd click misses (a typo in its text): `no-target` at that click.

    Not a typo in its scope: a click on the text just framed takes the frame's aim (walks.py `align`).
    """
    captures = walk()
    captures[2]["steps"] = [click("Domenny", within="Twoje usługi")]
    return captures


def no_fix(self: FakeClaude, ctx: Context, capture: Any, error: Any) -> Any:
    return {"bad": 1}  # Claude's fix is unusable: the walk stays failed at that click


def landed(app: App, did: str) -> dict[str, Any]:
    listing = held(app, did)
    assert listing["held"] == [], listing
    (out,) = listing["released"]
    return dict(out)


def test_the_capture_reply_of_a_failed_walk_carries_the_shots_before_it(app: App) -> None:
    use(app, Panel)
    did = draft(app)
    _, body = app.call(
        "POST", f"/api/drafts/{did}/capture", spec({"click": "Brak"}, {"shot": {"name": "end"}})
    )
    status, body = grant(app, body["request"]["id"])
    assert (status, body["error"], body["step"]) == (422, "no-target", 3), body
    assert [s["name"] for s in body["shots"]] == KEPT, body
    stored = {a["name"] for a in app.store.list_assets(did)}
    assert {s["asset"] for s in body["shots"]} == stored


def test_shots_before_the_failed_click_land_and_the_rest_is_missing(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeClaude, "fix_capture", no_fix)
    _, did, (_, body) = start(app, monkeypatch, broken_walk())
    grant(app, body["held"]["requests"][0]["id"])
    out = landed(app, did)
    assert out["partial"] and images(app, did, out["replacement"]) == KEPT, out
    assert out["dropped"] == [
        {"key": "domains", "reason": "no-target"},
        {"key": "menu", "reason": "no-target"},
    ]
    (prop,) = app.store.get(did)["proposals"]
    assert prop["missing"] == ["domains", "menu"]


def test_a_fix_run_that_fails_earlier_keeps_the_shots_already_taken(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix run dies before any shot (chromium broke): the first run's shots still land."""
    fixes: list[str] = []

    def fix(self: FakeClaude, ctx: Context, capture: Any, error: Any) -> Any:
        fixes.append(capture["key"])
        return {**capture, "steps": [click("Domeny", within="Twoje usługi")]}

    monkeypatch.setattr(FakeClaude, "fix_capture", fix)
    _, did, (_, body) = start(app, monkeypatch, broken_walk())
    monkeypatch.setattr(app.httpd.captures, "capture", lambda *a, **k: (502, {"error": "chromium-failed"}))
    grant(app, body["held"]["requests"][0]["id"])
    out = landed(app, did)
    assert fixes == ["domains"] and images(app, did, out["replacement"]) == KEPT, out
    assert [d["key"] for d in out["dropped"]] == ["domains", "menu"]


def test_a_walk_failing_before_any_shot_still_holds_as_failed(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None taken: never released as text-only (the rule kept shots must not weaken)."""
    monkeypatch.setattr(FakeClaude, "fix_capture", no_fix)
    captures = walk()
    captures[0] = {**captures[0], "highlight": aim("Brak")}  # the login page shot itself misses
    _, did, (status, body) = start(app, monkeypatch, captures)
    lost = failed_hold(app, did, status, body)
    assert {d["key"] for d in lost} == {"login", "dashboard", "domains", "menu"}, lost
    assert app.store.list_assets(did) == []


def test_a_failed_walk_whose_kept_shots_cannot_be_stored_still_answers_the_error(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(app, Panel)
    did = draft(app)
    _, body = app.call("POST", f"/api/drafts/{did}/capture", spec({"click": "Brak"}))
    monkeypatch.setattr(app.store, "add_asset", lambda *a: (_ for _ in ()).throw(AssetError("too-large")))
    status, body = grant(app, body["request"]["id"])
    assert (status, body["error"]) == (422, "no-target") and "shots" not in body, body


def test_a_second_login_page_shot_that_misses_keeps_the_first() -> None:
    login = "https://panel.example/security/login"
    two = {
        "url": login,
        "name": "w",
        "steps": [{"shot": {"name": "a"}}, {"shot": {"name": "b", "highlight": "Brak"}}],
    }
    with pytest.raises(errors.NoTarget) as caught:
        run_all(FakePage(["Zaloguj"]), parse(two))
    assert [name for name, _ in caught.value.taken] == ["a"]
