"""Claude's walkthrough (kuhy's cyber_Folks renewal guide) through propose: one run, one grant, four shots.

Fake browser (tests/capture_fakes.py), fake Claude reply: the shares URL is only in kuhy's
instruction, the login URL only in the draft. "btn:more" is the icon-only ⋮ of the domain row.
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.capture.policy import Target
from draft_studio.claude import Context
from draft_studio.claude_fake import FakeClaude
from draft_studio.held import HeldStore
from draft_studio.shotreq import marker
from tests.capture_fakes import FakePage
from tests.conftest import App
from tests.imgdata import png
from tests.test_capture_service import ShotPage, use
from tests.test_held import EFFECTS, SAVE, grant, held
from tests.test_shots import URL, failed_hold, propose, text_draft

SHARES = "https://panel.example/your-data/shares"
LOGIN = "https://panel.example/security/login"
IMPERSONATE = ("POST", "https://panel.example/api/impersonate")
KEYS = ["login", "dashboard", "domains", "menu"]
GUIDE = f"1. Otworzyć {LOGIN} i zalogować się.\n2. Kliknąć „Domeny”.\n3. Kliknąć ⋮ przy domenie.\n4. „Odnów”."
ASK = f"Dodaj zrzuty. Ja wchodzę przez {SHARES} i klikam „ZALOGUJ JAKO” przy właścicielce."


class Owner(FakePage):
    def __init__(self, password: bool = False) -> None:
        scopes = {
            "GABINET": ["ZALOGUJ JAKO", "ZAKOŃCZ UDOSTĘPNIANIE"],
            "Twoje usługi": ["Domeny"],
        }
        super().__init__(["Domeny", "Domeny", "Odnów"], {"ZALOGUJ JAKO": [IMPERSONATE]}, scopes)
        self.password = password

    def click(
        self, target: Target, allow_submit: bool, allowed: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """The domain row (and its ⋮) exists only once "Domeny" was opened: a click skipped for a
        goal that is already reached (capture/already.py) must not find it on the dashboard."""
        if target.text == "Domeny":
            self.scopes["kaliszpsycholog.pl"] = ["btn:more"]
        return super().click(target, allow_submit, allowed)

    def screenshot(self) -> bytes:
        return png(width=len(self.calls))  # distinct shots, distinct assets


def aim(text: str = "", role: str = "", within: str = "", nth: int = 0) -> dict[str, Any]:
    return {"text": text, "role": role, "within": within, "nth": nth}


def click(text: str = "", **kw: Any) -> dict[str, Any]:
    return {"kind": "click", **aim(text, **kw)}


def cap(key: str, steps: list[Any], highlight: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"key": key, "walk": "renew", "fresh": False, "url": SHARES, "steps": steps} | {
        "highlight": highlight,
        "alt": key.title(),
        **kw,
    }


MORE = {"role": "button", "within": "kaliszpsycholog.pl", "nth": -1}


def walk() -> list[dict[str, Any]]:
    return [
        cap("login", [], aim(), fresh=True, url=LOGIN),
        cap("dashboard", [click("ZALOGUJ JAKO", within="GABINET")], aim("Domeny", within="Twoje usługi")),
        cap("domains", [click("Domeny", within="Twoje usługi")], aim(**MORE)),
        cap("menu", [click(**MORE)], aim("Odnów")),
    ]


def reply(captures: list[dict[str, Any]]) -> Any:
    lines = [f"Krok {k}.\n\n{marker(k)}" for k in KEYS]
    return lambda self, ctx: {"replacement": "\n\n".join(lines), "captures": captures}


def start(app: App, monkeypatch: pytest.MonkeyPatch, captures: list[Any], page: Any = Owner) -> Any:
    launcher = use(app, page)
    monkeypatch.setattr(FakeClaude, "rewrite", reply(captures))
    did = text_draft(app, GUIDE)
    return launcher, did, propose(app, did, GUIDE, ASK)


def images(app: App, did: str, text: str) -> list[str]:
    names = [a["name"] for a in app.store.list_assets(did)]
    return [n.split("-")[0] for n in sorted(names, key=text.index)]


def test_the_walk_holds_once_then_lands_every_image_in_order(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, did, (status, body) = start(app, monkeypatch, walk())
    assert status == 202, body
    (req,) = body["held"]["requests"]
    assert (req["step"], req["detail"]) == (1, "POST https://panel.example/api/impersonate")
    assert grant(app, req["id"])[0] == 200
    (out,) = held(app, did)["released"]
    assert out["dropped"] == [] and "IMG" not in out["replacement"], out
    assert images(app, did, out["replacement"]) == KEYS
    assert "![Domains](assets/domains-" in out["replacement"]
    assert launcher.runs == 2 and app.call("GET", "/api/captures/pending")[1]["requests"] == []


def test_an_invented_goto_drops_the_whole_walk_unopened(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    captures = walk()
    captures[2]["steps"].insert(0, {"kind": "goto", **aim("https://panel.example/invented")})
    launcher, did, (status, body) = start(app, monkeypatch, captures)
    assert failed_hold(app, did, status, body) == [{"key": k, "reason": "url"} for k in KEYS], body
    assert launcher.runs == 0 and app.store.list_assets(did) == []


@pytest.mark.parametrize(
    "broken",
    [
        lambda c: c[1].update(steps="x"),  # one bad capture
        lambda c: c[2].update(url="https://panel.example/other"),  # two start URLs
        lambda c: c[0].update(steps=[click("Domeny")]),  # a fresh shot takes no clicks
        lambda c: c[1]["steps"].append(click()),  # a click with no aim
    ],
)
def test_a_malformed_walk_drops_whole(app: App, monkeypatch: pytest.MonkeyPatch, broken: Any) -> None:
    captures = walk()
    broken(captures)
    launcher, did, (status, body) = start(app, monkeypatch, captures)
    assert failed_hold(app, did, status, body) == [{"key": k, "reason": "bad-spec"} for k in KEYS], body
    assert launcher.runs == 0


def test_a_walk_behind_a_login_is_held_and_retried_as_one_run(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = iter([Owner(password=True), Owner(password=True), Owner()])
    launcher, did, (status, body) = start(app, monkeypatch, walk(), lambda: next(pages))
    assert status == 202 and body["held"]["login"] == ["https://panel.example"], body
    hid = body["held"]["id"]
    listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": hid})[1]
    assert listing["held"][0]["login"] == ["https://panel.example"] and launcher.runs == 2
    listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": hid})[1]
    assert listing["held"][0]["requests"][0]["step"] == 1 and launcher.runs == 3


def test_a_missed_click_is_fixed_on_its_own_shot_and_the_walk_reruns(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    captures = walk()
    captures[2]["steps"] = [click("Domeny", within="Twoje uslugi")]
    asked: list[tuple[str, Any]] = []

    def fix(self: FakeClaude, ctx: Context, capture: Any, error: Any) -> Any:
        asked.append((capture["key"], error["step"]))
        return {**capture, "steps": [click("Domeny", within="Twoje usługi")]}

    monkeypatch.setattr(FakeClaude, "fix_capture", fix)
    launcher, did, (_, body) = start(app, monkeypatch, captures)
    status, again = grant(app, body["held"]["requests"][0]["id"])
    assert status == 422 and asked == [("domains", 0)], again
    listing = held(app, did)  # the fix run reused the impersonation grant (capture/reuse.py): no card
    assert listing["held"] == [] and app.call("GET", "/api/captures/pending")[1]["requests"] == []
    (out,) = listing["released"]
    assert images(app, did, out["replacement"]) == KEYS and launcher.runs == 3


def test_a_missed_highlight_names_its_shot_with_no_step(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    captures = walk()[:3]
    captures[2]["highlight"] = aim(role="button", within="inna-domena.pl")
    asked: list[tuple[str, Any]] = []

    def fix(self: FakeClaude, ctx: Context, capture: Any, error: Any) -> Any:
        asked.append((capture["key"], error["step"]))
        return {"bad": 1}

    monkeypatch.setattr(FakeClaude, "fix_capture", fix)
    _, did, (_, body) = start(app, monkeypatch, captures)
    grant(app, body["held"]["requests"][0]["id"])
    (out,) = held(app, did)["released"]  # the shots before the missed one land (tests/test_shots_keep.py)
    assert asked == [("domains", None)] and images(app, did, out["replacement"]) == ["login", "dashboard"]
    reasons = {d["key"]: d["reason"] for d in out["dropped"]}
    assert reasons == {"domains": "no-target", "menu": "no-request"}


def test_an_empty_shots_reply_drops_every_slot_without_images(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeClaude, "rewrite", reply(walk()))
    use(app, Owner)
    monkeypatch.setattr(app.httpd.captures, "capture", lambda *a, **k: (200, {"shots": []}))
    did = text_draft(app, GUIDE)
    assert failed_hold(app, did, *propose(app, did, GUIDE, ASK)) == [
        {"key": k, "reason": "not-shot"} for k in KEYS
    ]
    assert app.store.list_assets(did) == []


def test_a_fresh_capture_outside_a_walk_drops(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    alone = cap("login", [], aim(), fresh=True, url=LOGIN, walk="")
    launcher, did, (status, body) = start(app, monkeypatch, [alone])
    assert {"key": "login", "reason": "bad-spec"} in failed_hold(app, did, status, body), body
    assert launcher.runs == 0


def test_a_held_record_from_before_walks_still_releases(app: App) -> None:
    """Live held records (7 days) predate `walks`, aims and `fresh`: they run as standalone captures."""
    use(app, lambda: ShotPage(["Zapisz"], EFFECTS))
    did = text_draft(app, SAVE)
    body = propose(app, did, SAVE)[1]
    store = HeldStore(app.store)
    rec = store.get(did, body["held"]["id"])
    del rec["walks"]
    old = {"key": "shot-1", "url": URL, "steps": [{"kind": "click", "text": "Zapisz"}], "highlight": "Zapisz"}
    rec["shots"]["shot-1"]["shot"] = {**old, "alt": "Zapisz"}
    store.put(rec)
    assert grant(app, body["held"]["requests"][0]["id"])[0] == 200
    (out,) = held(app, did)["released"]
    assert out["replacement"].startswith(f"{SAVE}\n\n![Zapisz](assets/shot-1-"), out


def test_the_fake_walk_reads_the_guide_and_kuhys_setup(app: App) -> None:
    """FakeClaude `[walk]` (the editor E2E's Claude) shapes the same walk through the real pipeline."""
    use(app, Owner)
    guide = (
        f"1. Otworzyć {LOGIN}.\n2. Kliknąć „Domeny” w „Twoje usługi”.\n"
        "3. Kliknąć ⋮ w „kaliszpsycholog.pl”.\nBez kroku.\n4. „Odnów”."
    )
    did = text_draft(app, guide)
    status, body = propose(app, did, guide, f"[walk] {SHARES}: „ZALOGUJ JAKO” w „GABINET”")
    assert status == 202, body
    assert grant(app, body["held"]["requests"][0]["id"])[0] == 200
    (out,) = held(app, did)["released"]
    assert out["dropped"] == [] and images(app, did, out["replacement"]) == ["shot"] * 4
    assert ")\n\nBez kroku.\n4. „Odnów”.\n\n![shot-4](assets/" in out["replacement"], out["replacement"]
    assert propose(app, did, guide, "[walk] no url")[1]["replacement"] == guide
