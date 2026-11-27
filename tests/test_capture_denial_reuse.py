"""A denial is remembered for its walk like a grant (kuhy, 2026-11-27), on a fake clock.

kuhy denied the robofolks chat POST once; Claude's fix runs (`follows`) carried no denials and
carded it again every time. Now the same method + URL is failed again, no card, the shot still
taken, for REUSE_S seconds, same walk and draft. A denial only ever blocks more.
"""

from __future__ import annotations

import pytest

from draft_studio.capture.reuse import REUSE_S, Ledger
from draft_studio.capture.service import CaptureService
from draft_studio.capture.spec import Denial, Grant, parse
from tests.conftest import App
from tests.test_capture_partial import CHAT, IMPERSONATE, PRE_CHAT, SPEC
from tests.test_capture_reuse import Clock
from tests.test_capture_service import FakeLauncher, ShotPage, draft


class Rig:
    """A walk whose click sends the impersonation and the chat POST; kuhy allows only the first."""

    def __init__(self, app: App) -> None:
        self.pages: list[ShotPage] = []
        self.clock = Clock()

        def make() -> ShotPage:
            self.pages.append(ShotPage(["ZALOGUJ JAKO", "Domeny"], {"ZALOGUJ JAKO": [IMPERSONATE, CHAT]}))
            return self.pages[-1]

        self.launcher = FakeLauncher(make)
        self.svc = CaptureService(app.store, self.launcher, clock=self.clock)
        self.did = draft(app)

    def partly_granted(self) -> str:
        """First run cards both POSTs, kuhy allows the impersonation only: the card's id."""
        status, body = self.svc.capture(self.did, SPEC)
        assert status == 409, body
        rid = str(body["request"]["id"])
        assert self.svc.grant(rid, {"allow": [list(IMPERSONATE)]})[0] == 200
        return rid

    def sent(self) -> list[tuple[str, str]]:
        """What the click of the last run was allowed to send."""
        clicks = [c for c in self.pages[-1].calls if c[0] == "click"]
        return list(clicks[-1][3])


def test_a_fix_run_does_not_card_a_denied_pair_again(app: App) -> None:
    r = Rig(app)
    rid = r.partly_granted()
    status, body = r.svc.capture(r.did, SPEC, follows=rid)
    assert status == 200 and "asset" in body, body
    assert r.svc.list_pending()[1]["requests"] == []
    assert IMPERSONATE in r.sent() and CHAT not in r.sent() and PRE_CHAT not in r.sent()


def test_a_full_deny_is_remembered_too(app: App) -> None:
    r = Rig(app)
    rid = str(r.svc.capture(r.did, SPEC)[1]["request"]["id"])
    assert r.svc.deny(rid)[0] == 200  # both POSTs denied, the shot lands
    status, _ = r.svc.capture(r.did, SPEC, follows=rid)
    assert status == 200 and r.svc.list_pending()[1]["requests"] == []
    assert IMPERSONATE not in r.sent() and CHAT not in r.sent()


def test_a_denial_expires_after_ten_minutes(app: App) -> None:
    r = Rig(app)
    rid = r.partly_granted()
    r.clock.now += REUSE_S - 1
    assert r.svc.capture(r.did, SPEC, follows=rid)[0] == 200
    r.clock.now += 1
    status, body = r.svc.capture(r.did, SPEC, follows=rid)
    assert status == 409 and CHAT in [tuple(b) for b in body["request"]["blocked"]], body  # grant expired too


def test_another_walk_or_draft_still_cards_it(app: App) -> None:
    r = Rig(app)
    rid = r.partly_granted()
    other = str(r.svc.capture(r.did, SPEC)[1]["request"]["id"])  # a new walk
    assert r.svc.capture(r.did, SPEC, follows=other)[0] == 409
    assert r.svc.capture(r.did, SPEC)[0] == 409  # follows nothing
    assert r.svc.capture(draft(app), SPEC, follows=rid)[0] == 409  # another draft, same rid


def test_a_denial_is_never_written_into_a_new_card(app: App) -> None:
    """Only kuhy's own decisions ride in a record: a remembered one must not outlive its window."""
    r = Rig(app)
    rid = r.partly_granted()
    r.launcher.make = lambda: ShotPage(
        ["ZALOGUJ JAKO", "Domeny"], {"ZALOGUJ JAKO": [CHAT, ("POST", "https://x.example/t")]}
    )
    status, body = r.svc.capture(r.did, SPEC, follows=rid)
    assert status == 409 and [tuple(b) for b in body["request"]["blocked"]] == [
        ("POST", "https://x.example/t")
    ]
    assert [tuple(x) for d in body["request"]["denials"] for x in d["requests"]] == []


def test_a_denial_only_blocks_never_allows() -> None:
    ledger, walk = (
        Ledger(Clock()),
        parse(
            {"url": SPEC["url"], "name": "w", "steps": [{"click": "ZALOGUJ JAKO"}, {"shot": {"name": "a"}}]}
        ),
    )
    ledger.remember("w", "d", Grant(0, click=False, requests=(IMPERSONATE, CHAT)))
    ledger.deny("w", "d", Denial(0, click=False, requests=(CHAT,)))
    assert ledger.denials("w", "d") == [Denial(0, click=False, requests=(CHAT,))]
    assert all(CHAT not in g.requests and not g.click for g in ledger.grants("w", "d", walk, []))
    assert ledger.denials("w", "other") == [] and ledger.denials("nope", "d") == []


def test_a_later_grant_lifts_the_denial() -> None:
    ledger = Ledger(Clock())
    ledger.deny("w", "d", Denial(0, click=False, requests=(CHAT,)))
    ledger.remember("w", "d", Grant(0, click=False, requests=(CHAT,)))
    assert ledger.denials("w", "d") == []


@pytest.mark.parametrize("click", [True, False])
def test_only_request_denials_are_kept(click: bool) -> None:
    """A denied click names a step index a fix run may move: it is never kept."""
    ledger = Ledger(Clock())
    ledger.deny("w", "d", Denial(2, click=click, requests=() if click else (CHAT,)))
    assert bool(ledger.denials("w", "d")) is (not click)
