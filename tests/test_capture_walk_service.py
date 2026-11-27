"""A walkthrough over the capture HTTP API: one grant, one run, every shot stored in order."""

from __future__ import annotations

from typing import Any

from tests.capture_fakes import FakePage
from tests.conftest import App
from tests.imgdata import png
from tests.test_capture_service import draft, use

MAIN = "https://panel.example/your-data/shares"
IMPERSONATE = ("POST", "https://panel.example/api/impersonate")
TRACK = ("POST", "https://panel.example/api/track")


class Panel(FakePage):
    def __init__(self) -> None:
        super().__init__(["Domeny"], effects={"ZALOGUJ JAKO": [IMPERSONATE], "Domeny": [TRACK]})
        self.scopes = {"GABINET": ["ZALOGUJ JAKO", "ZAKOŃCZ UDOSTĘPNIANIE"]}

    def screenshot(self) -> bytes:
        return png(width=len(self.calls))  # distinct shots, distinct assets


def spec(*more: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": MAIN,
        "name": "renew",
        "steps": [
            {"shot": {"name": "login", "fresh": True, "url": "https://panel.example/security/login"}},
            {"click": {"text": "ZALOGUJ JAKO", "within": "GABINET"}},
            {"shot": {"name": "dashboard", "highlight": "Domeny"}},
            *more,
        ],
    }


def test_one_grant_then_every_shot_lands_from_one_run(app: App) -> None:
    launcher = use(app, Panel)
    did = draft(app)
    status, body = app.call("POST", f"/api/drafts/{did}/capture", spec())
    assert status == 409 and body["request"]["step"] == 1, body
    assert body["request"]["stepText"] == "ZALOGUJ JAKO within „GABINET”"
    status, body = app.call("POST", f"/api/captures/{body['request']['id']}/grant")
    assert status == 200 and [s["name"] for s in body["shots"]] == ["login", "dashboard"], body
    for shot in body["shots"]:
        assert shot["asset"].startswith(f"{shot['name']}-") and shot["md"] == f"![](assets/{shot['asset']})"
    assert launcher.runs == 2 and app.call("GET", "/api/captures/pending")[1]["requests"] == []


def test_a_later_refusal_asks_again_for_the_earlier_grant(app: App) -> None:
    """Pinned: a grant is spent by one run, and a re-run starts over, so it asks again (never resent)."""
    use(app, Panel)
    did = draft(app)
    _, body = app.call("POST", f"/api/drafts/{did}/capture", spec({"click": "Domeny"}))
    _, body = app.call("POST", f"/api/captures/{body['request']['id']}/grant")
    assert body["request"]["step"] == 3 and body["request"]["grants"] == []  # step 1's grant was spent
    _, body = app.call("POST", f"/api/captures/{body['request']['id']}/grant")
    assert body["error"] == "needs-permission" and body["request"]["step"] == 1


def test_a_denied_impersonation_still_returns_the_shots(app: App) -> None:
    use(app, Panel)
    did = draft(app)
    _, body = app.call("POST", f"/api/drafts/{did}/capture", spec())
    status, body = app.call("POST", f"/api/captures/{body['request']['id']}/deny")
    assert status == 200 and [s["name"] for s in body["shots"]] == ["login", "dashboard"]
