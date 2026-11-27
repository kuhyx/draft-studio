"""Capture HTTP API end to end over the real server, with a fake browser behind it."""

from __future__ import annotations

import contextlib
import threading
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from draft_studio.capture import errors
from draft_studio.capture.service import CaptureService
from tests.capture_fakes import FakePage
from tests.conftest import App
from tests.imgdata import png

SPEC = {"url": "https://panel.example/", "name": "dns-1", "steps": [{"click": "Zapłać"}], "highlight": "Done"}


class FakeLauncher:
    def __init__(self, make: Any) -> None:
        self.make = make
        self.runs = 0
        self.held = False

    def busy(self) -> bool:
        return self.held

    @contextlib.contextmanager
    def open(self) -> Iterator[Any]:
        self.runs += 1
        page = self.make()
        if isinstance(page, Exception):
            raise page
        yield page


class ShotPage(FakePage):
    def screenshot(self) -> bytes:
        return png()


def use(app: App, make: Any) -> FakeLauncher:
    launcher = FakeLauncher(make)
    app.httpd.captures = CaptureService(app.store, launcher)
    return launcher


def draft(app: App) -> str:
    return str(app.store.create("text")["id"])


def test_capture_saves_an_asset(app: App) -> None:
    use(app, lambda: ShotPage(["Done"]))
    did = draft(app)
    status, body = app.call("POST", f"/api/drafts/{did}/capture", {**SPEC, "steps": []})
    assert status == 200 and body["asset"].startswith("dns-1-") and body["asset"].endswith(".png")
    assert body["md"] == f"![](assets/{body['asset']})"
    assert [a["name"] for a in app.store.list_assets(did)] == [body["asset"]]


def test_permission_lifecycle_over_http(app: App) -> None:
    page_effects = {"Zapłać": [("POST", "https://panel.example/pay")]}
    launcher = use(app, lambda: ShotPage(["Zapłać", "Done"], page_effects))
    did = draft(app)
    status, body = app.call("POST", f"/api/drafts/{did}/capture", SPEC)
    assert status == 409 and body["error"] == "needs-permission"
    first = body["request"]
    assert (first["reason"], first["stepText"], first["draftId"]) == ("deny-list", "Zapłać", did)
    status, listed = app.call("GET", "/api/captures/pending")
    assert status == 200 and [r["id"] for r in listed["requests"]] == [first["id"]]
    with urllib.request.urlopen(app.base + first["shot"], timeout=5) as resp:
        assert resp.headers["Content-Type"] == "image/png" and resp.read() == png()

    # Granting the click lets it through; its POST is a new, separate request.
    status, body = app.call("POST", f"/api/captures/{first['id']}/grant", {})
    assert status == 409 and body["request"]["reason"] == "request"
    second = body["request"]
    assert second["grants"] == [] and second["allow"]["click"] is True
    assert app.call("POST", f"/api/captures/{first['id']}/grant", {}) == (409, {"error": "not-pending"})

    status, body = app.call("POST", f"/api/captures/{second['id']}/grant", {})
    assert status == 200 and body["asset"].startswith("dns-1-")
    assert launcher.runs == 3
    assert app.call("GET", "/api/captures/pending") == (200, {"requests": []})


def test_deny_of_a_refused_click_shoots_the_page_before_it(app: App) -> None:
    launcher = use(app, lambda: ShotPage(["Zapłać", "Done"]))
    did = draft(app)
    _, body = app.call("POST", f"/api/drafts/{did}/capture", SPEC)
    rid = body["request"]["id"]
    status, out = app.call("POST", f"/api/captures/{rid}/deny", {})
    assert status == 200 and out["asset"].startswith("dns-1-") and launcher.runs == 2
    assert app.call("POST", f"/api/captures/{rid}/grant", {}) == (409, {"error": "not-pending"})
    assert app.call("POST", f"/api/captures/{rid}/deny", {}) == (409, {"error": "not-pending"})
    assert app.call("POST", "/api/captures/0123456789abcdef/deny", {}) == (404, {"error": "not-found"})


def test_deny_of_a_request_goes_on_and_asks_again_only_for_something_new(app: App) -> None:
    track, pay = ("POST", "https://panel.example/track"), ("POST", "https://panel.example/pay")
    launcher = use(app, lambda: ShotPage(["Domeny", "Zapłać", "Done"], {"Domeny": [track], "Zapłać": [pay]}))
    did = draft(app)
    spec = {**SPEC, "steps": [{"click": "Domeny"}, {"click": "Zapłać"}]}
    _, body = app.call("POST", f"/api/drafts/{did}/capture", spec)
    first = body["request"]
    assert first["detail"] == "POST https://panel.example/track"
    status, body = app.call("POST", f"/api/captures/{first['id']}/deny", {})
    assert status == 409 and body["request"]["stepText"] == "Zapłać"  # the track call no longer asks
    second = body["request"]
    assert second["denials"] == [{"step": 0, "click": False, "requests": [list(track)]}]
    status, body = app.call("POST", f"/api/captures/{second['id']}/deny", {})  # the deny-listed click
    assert status == 200 and body["asset"].startswith("dns-1-")
    assert launcher.runs == 3
    assert app.call("GET", "/api/captures/pending") == (200, {"requests": []})


def test_deny_on_a_busy_profile_keeps_the_request(app: App) -> None:
    launcher = use(app, lambda: ShotPage(["Zapłać", "Done"]))
    did = draft(app)
    _, body = app.call("POST", f"/api/drafts/{did}/capture", SPEC)
    rid = body["request"]["id"]
    launcher.held = True
    assert app.call("POST", f"/api/captures/{rid}/deny", {}) == (409, {"error": "profile-busy"})
    launcher.held = False
    assert app.call("POST", f"/api/captures/{rid}/deny", {})[0] == 200


@pytest.mark.parametrize(
    ("make", "status", "body"),
    [
        (errors.NoChromium, 503, {"error": "no-chromium"}),
        (errors.CaptureTimeout, 504, {"error": "timeout"}),
        (errors.ProfileBusy, 409, {"error": "profile-busy"}),
    ],
)
def test_named_failures(app: App, make: Any, status: int, body: dict[str, Any]) -> None:
    use(app, make)
    assert app.call("POST", f"/api/drafts/{draft(app)}/capture", SPEC) == (status, body)


def test_login_required_carries_the_origin(app: App) -> None:
    def make() -> FakePage:
        page = ShotPage([])
        page.password = True
        return page

    use(app, make)
    status, body = app.call("POST", f"/api/drafts/{draft(app)}/capture", SPEC)
    assert (status, body) == (
        409,
        {"error": "login-required", "origin": "https://panel.example", "url": SPEC["url"]},
    )


def test_bad_requests(app: App) -> None:
    use(app, lambda: ShotPage([]))
    did = draft(app)
    assert app.call("POST", f"/api/drafts/{did}/capture", {"url": "file:///x", "name": "a"}) == (
        400,
        {"error": "bad-spec", "detail": "url"},
    )
    assert app.call("POST", "/api/drafts/nosuchdraft/capture", SPEC)[0] == 404
    assert app.call("GET", f"/api/drafts/{did}/capture")[0] == 404
    assert app.call("POST", "/api/captures/0123456789abcdef/grant", {}) == (404, {"error": "not-found"})
    assert app.call("POST", "/api/captures/0123456789abcdef/deny", {}) == (404, {"error": "not-found"})
    assert app.call("GET", "/api/captures/0123456789abcdef/shot") == (404, {"error": "not-found"})
    assert app.call("POST", "/api/captures/pending")[0] == 404
    assert app.call("GET", "/api/captures/0123456789abcdef/deny")[0] == 404


def test_bad_image_bytes_are_named(app: App) -> None:
    use(app, lambda: FakePage(["Done"]))  # FakePage's screenshot is not a decodable PNG
    status, body = app.call("POST", f"/api/drafts/{draft(app)}/capture", {**SPEC, "steps": []})
    assert (status, body) == (502, {"error": "bad-image"})


def test_one_capture_at_a_time_and_busy_profile_keeps_the_grant(app: App) -> None:
    gate, inside = threading.Event(), threading.Event()

    class SlowPage(ShotPage):
        def goto(self, url: str) -> None:
            inside.set()
            gate.wait(5)
            super().goto(url)

    launcher = use(app, lambda: SlowPage(["Done"]))
    did = draft(app)
    results: list[Any] = []
    worker = threading.Thread(
        target=lambda: results.append(app.call("POST", f"/api/drafts/{did}/capture", {**SPEC, "steps": []}))
    )
    worker.start()
    assert inside.wait(5)
    assert app.call("POST", f"/api/drafts/{did}/capture", SPEC) == (409, {"error": "profile-busy"})
    gate.set()
    worker.join(5)
    assert results[0][0] == 200

    launcher.make = lambda: ShotPage(["Zapłać", "Done"])
    _, body = app.call("POST", f"/api/drafts/{did}/capture", SPEC)
    launcher.held = True
    rid = body["request"]["id"]
    assert app.call("POST", f"/api/captures/{rid}/grant", {}) == (409, {"error": "profile-busy"})
    assert [r["id"] for r in app.call("GET", "/api/captures/pending")[1]["requests"]] == [rid]


def test_default_service_has_no_chromium_in_tests(app: App) -> None:
    status, body = app.call("POST", f"/api/drafts/{draft(app)}/capture", SPEC)
    assert (status, body) == (503, {"error": "no-chromium"})


def test_a_granted_request_is_resent_only_by_its_walks_re_runs(app: App) -> None:
    a, b = ("POST", "https://panel.example/a"), ("POST", "https://panel.example/b")
    pages: list[FakePage] = []

    def make() -> FakePage:
        pages.append(ShotPage(["A", "B", "Done"], {"A": [a], "B": [b]}))
        return pages[-1]

    use(app, make)
    spec = {**SPEC, "steps": [{"click": "A"}, {"click": "B"}]}
    _, body = app.call("POST", f"/api/drafts/{draft(app)}/capture", spec)
    asked: list[int] = []
    while body.get("error") == "needs-permission":
        asked.append(body["request"]["step"])
        _, body = app.call("POST", f"/api/captures/{body['request']['id']}/grant", {})
    assert body["asset"].startswith("dns-1-")
    # A is approved once and sent by both re-runs of this walk (capture/reuse.py, 10 minutes).
    assert asked == [0, 1]
    sent_a = [c for p in pages for c in p.calls if c[0] == "click" and c[1] == "A" and a in c[3]]
    assert len(sent_a) == 2
