"""Held answers: permission, login and busy holds, grant chains, deny, expiry, collection."""

from __future__ import annotations

import json
from typing import Any

from draft_studio.capture import errors
from tests.conftest import App
from tests.test_capture_service import FakeLauncher, ShotPage, use
from tests.test_shots import URL, propose, text_draft

SAVE = f"Otworzyć {URL} i kliknąć „Zapisz”."
EFFECTS = {"Zapisz": [("POST", f"{URL}api/save")], "Zapłać": [("POST", f"{URL}api/pay")]}


def saving(app: App, texts: tuple[str, ...] = ("Zapisz",)) -> FakeLauncher:
    return use(app, lambda: ShotPage(list(texts), EFFECTS))


def held(app: App, did: str) -> dict[str, Any]:
    status, body = app.call("GET", f"/api/drafts/{did}/held")
    assert status == 200
    return dict(body)


def grant(app: App, rid: str) -> tuple[int, Any]:
    return app.call("POST", f"/api/captures/{rid}/grant", {})


def hold(app: App, text: str = SAVE) -> tuple[str, dict[str, Any]]:
    did = text_draft(app, text)
    status, body = propose(app, did, text)
    assert status == 202, body
    return did, body["held"]


def test_permission_holds_then_grant_releases_with_the_image(app: App) -> None:
    saving(app)
    did, h = hold(app)
    assert (h["kind"], h["from"], h["to"], h["login"], h["busy"]) == ("proposal", 0, len(SAVE), [], False)
    (req,) = h["requests"]
    assert req["reason"] == "request" and req["draftId"] == did
    listed = held(app, did)
    assert [x["id"] for x in listed["held"]] == [h["id"]] and listed["released"] == []

    assert grant(app, req["id"])[0] == 200
    (out,) = held(app, did)["released"]
    (asset,) = [a["name"] for a in app.store.list_assets(did)]
    assert out["replacement"] == f"{SAVE}\n\n![Zapisz](assets/{asset})"
    assert (out["id"], out["kind"], out["span"], out["previous"], out["dropped"]) == (
        h["id"],
        "proposal",
        SAVE,
        "",
        [],
    )
    assert app.call("POST", f"/api/drafts/{did}/held", {"collect": [h["id"], h["id"], "f" * 16]}) == (
        200,
        {"collected": 1},
    )
    assert held(app, did) == {"held": [], "released": []}


def test_a_grant_chain_relinks_the_hold(app: App) -> None:
    saving(app, ("Zapłać",))
    did, h = hold(app, f"Otworzyć {URL} i kliknąć „Zapłać”.")
    first = h["requests"][0]
    assert first["reason"] == "deny-list"
    status, body = grant(app, first["id"])
    assert status == 409 and body["request"]["reason"] == "request"
    (again,) = held(app, did)["held"]
    assert [r["id"] for r in again["requests"]] == [body["request"]["id"]]
    assert grant(app, body["request"]["id"])[0] == 200
    assert "](assets/" in held(app, did)["released"][0]["replacement"]


def test_deny_releases_without_the_image_line(app: App) -> None:
    saving(app)
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    assert app.call("POST", f"/api/captures/{rid}/deny", {})[0] == 200
    assert app.call("POST", f"/api/captures/{rid}/deny", {})[0] == 409  # a second deny changes nothing
    (out,) = held(app, did)["released"]
    assert out["replacement"] == SAVE and out["dropped"] == [{"key": "shot-1", "reason": "denied"}]
    assert app.store.list_assets(did) == []


def test_login_hold_is_retried_after_login(app: App) -> None:
    def login_page() -> ShotPage:
        p = ShotPage(["Zapisz"])
        p.password = True
        return p

    launcher = use(app, login_page)
    did, h = hold(app)
    assert (h["requests"], h["login"]) == ([], ["https://panel.example"])
    launcher.make = lambda: ShotPage(["Zapisz"])
    status, listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": h["id"]})
    assert status == 200 and listing["held"] == [] and "](assets/" in listing["released"][0]["replacement"]
    assert app.call("POST", f"/api/drafts/{did}/held", {"retry": h["id"]})[0] == 200  # released: no-op
    assert app.call("POST", f"/api/drafts/{did}/held", {"retry": "0" * 16}) == (404, {"error": "not-found"})
    assert app.call("POST", f"/api/drafts/{did}/held", {"what": 1}) == (400, {"error": "bad-held"})


def test_busy_profile_holds_instead_of_dropping(app: App) -> None:
    launcher = use(app, errors.ProfileBusy)
    did, h = hold(app)
    assert h["busy"] is True
    launcher.make = lambda: ShotPage(["Zapisz"])
    _, listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": h["id"]})
    assert "](assets/" in listing["released"][0]["replacement"]


def test_a_grant_on_a_busy_profile_keeps_the_hold(app: App) -> None:
    launcher = saving(app)
    did, h = hold(app)
    launcher.held = True
    assert grant(app, h["requests"][0]["id"]) == (409, {"error": "profile-busy"})
    assert [x["id"] for x in held(app, did)["held"]] == [h["id"]]


def test_a_spent_grant_on_a_busy_profile_waits_for_a_retry(app: App) -> None:
    launcher = saving(app)
    did, h = hold(app)
    launcher.make = errors.ProfileBusy  # chromium refuses after the grant was claimed
    assert grant(app, h["requests"][0]["id"]) == (409, {"error": "profile-busy"})
    (again,) = held(app, did)["held"]
    assert again["busy"] is True and again["requests"] == []


def test_expired_and_elsewhere_denied_requests_release(app: App) -> None:
    saving(app)
    did, h = hold(app)
    (app.store.root / "captures" / f"{h['requests'][0]['id']}.json").unlink()
    assert held(app, did)["released"][0]["dropped"] == [{"key": "shot-1", "reason": "expired"}]

    did, h = hold(app)
    app.httpd.captures.pending.deny(h["requests"][0]["id"])
    assert held(app, did)["released"][0]["dropped"] == [{"key": "shot-1", "reason": "denied"}]

    did, h = hold(app)
    path = app.store.root / "drafts" / did / "held" / f"{h['id']}.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "created": 0}))
    assert held(app, did)["released"][0]["dropped"] == [{"key": "shot-1", "reason": "expired"}]


def test_a_grant_for_a_pruned_request_drops_the_line(app: App) -> None:
    saving(app)
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    (app.store.root / "captures" / f"{rid}.json").unlink()
    assert grant(app, rid) == (404, {"error": "not-found"})
    assert held(app, did)["released"][0]["dropped"] == [{"key": "shot-1", "reason": "expired"}]


def test_a_rerun_after_a_grant_may_need_a_fix(app: App) -> None:
    launcher = saving(app, ("Zapłać",))
    did, h = hold(app, f"Otworzyć {URL} i kliknąć „Zapłać”.")
    launcher.make = lambda: ShotPage(["Zaplac"], {})
    status, body = grant(app, h["requests"][0]["id"])
    assert status == 422 and body["error"] == "no-target"
    (again,) = held(app, did)["held"]  # fixed to „Zaplac”, which the deny-list stops again
    assert again["requests"][0]["stepText"] == "Zaplac"


def test_first_draft_is_held_and_saved_on_release(app: App) -> None:
    saving(app)
    did = str(app.store.create("mail")["id"])
    status, body = app.call("POST", f"/api/drafts/{did}/brief", {"brief": f"[noq] [shot] {SAVE}"})
    assert status == 202 and body["held"]["kind"] == "draft" and body["held"]["from"] is None
    assert app.store.get(did)["meta"]["phase"] != "editing"
    assert grant(app, body["held"]["requests"][0]["id"])[0] == 200
    (out,) = held(app, did)["released"]
    state = app.store.get(did)
    assert out["kind"] == "draft" and out["state"]["text"] == state["text"]
    assert state["meta"]["phase"] == "editing" and "](assets/" in state["text"]
    assert state["meta"]["subject"]


def test_held_routes_need_a_draft(app: App) -> None:
    assert app.call("GET", "/api/drafts/nosuch/held") == (404, {"error": "not-found"})
