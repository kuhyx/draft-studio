"""A permission card and its held answer live on disk: a new server answers them where the old one left off."""

from __future__ import annotations

from draft_studio.capture.service import CaptureService
from tests.conftest import App
from tests.test_capture_service import ShotPage
from tests.test_held import EFFECTS, SAVE, FakeLauncher, grant, held, hold, saving


def restart(app: App) -> None:
    """What a restart drops: every in-memory object of the server (reuse ledger, locks, caches)."""
    app.httpd.captures = CaptureService(app.store, FakeLauncher(lambda: ShotPage(["Zapisz"], EFFECTS)))


def test_a_card_waiting_for_kuhy_is_answerable_after_a_restart(app: App) -> None:
    saving(app)
    did, h = hold(app)
    (req,) = h["requests"]
    restart(app)
    status, pending = app.call("GET", "/api/captures/pending")
    assert status == 200 and [r["id"] for r in pending["requests"]] == [req["id"]]
    assert [x["id"] for x in held(app, did)["held"]] == [h["id"]]
    assert grant(app, req["id"])[0] == 200  # the new server re-runs the capture
    (out,) = held(app, did)["released"]
    assert out["id"] == h["id"] and "](assets/" in out["replacement"] and SAVE in out["replacement"]


def test_a_denied_card_is_answerable_after_a_restart_too(app: App) -> None:
    saving(app)
    did, h = hold(app)
    restart(app)
    rid = h["requests"][0]["id"]
    assert app.call("POST", f"/api/captures/{rid}/deny", {})[0] == 200
    assert len(held(app, did)["released"]) == 1
