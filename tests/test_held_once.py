"""One grant, one write: a decision re-run after a restart never sends a write its cut run already sent.

The cut run journals each granted write (capture/spent.py, fsync'd into the request's record) right
before the guard lets it leave; the replay fails that pair like a denial and still takes the shots.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture import spent
from draft_studio.capture.pending import PendingStore
from draft_studio.capture.policy import Target
from draft_studio.capture.spec import Grant, parse
from tests.held_rig import Rig
from tests.test_capture_service import ShotPage
from tests.test_held import EFFECTS, held, hold
from tests.test_shots import URL

SAVE = ("POST", f"{URL}api/save")
CUT = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)  # a cut kills a thread


class Site:
    """What reached the site, shared by every run of every server."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []


class SendPage(ShotPage):
    """A fake tab whose allowed writes go through the guard's journal hook, then reach `site`.

    `cut`: the server dies (SystemExit) right after the write left. `broken`: a failed save leaves
    the page on an error screen without the clicked element (the walk cannot go on without it).
    """

    def __init__(self, site: Site, *, cut: bool, broken: bool) -> None:
        super().__init__(["Zapisz"], EFFECTS)
        self.site, self.cut, self.broken = site, cut, broken

    def click(
        self, target: Target, allow_submit: bool, allowed: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        blocked = super().click(target, allow_submit, allowed)
        for request in self.effects.get(target.text, []):
            if request in allowed:
                spent.note(request)  # what the guard does right before Fetch.continueRequest
                self.site.writes.append(request)
                if self.cut:
                    raise SystemExit
            elif self.broken:
                self.texts = ["Błąd"]
        return blocked


class SendLauncher:
    """Run number `cut_at` is cut right after its granted write left."""

    def __init__(self, site: Site, cut_at: int = 0, broken: bool = False) -> None:
        self.site, self.cut_at, self.broken = site, cut_at, broken
        self.runs = 0
        self.pages: list[SendPage] = []

    def busy(self) -> bool:
        return False

    @contextlib.contextmanager
    def open(self) -> Iterator[Any]:
        self.runs += 1
        page = SendPage(self.site, cut=self.runs == self.cut_at, broken=self.broken)
        self.pages.append(page)
        yield page


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    found = Rig(tmp_path)
    yield found
    found.close()


def clicks(page: SendPage) -> list[list[tuple[str, str]]]:
    return [list(c[3]) for c in page.calls if c[0] == "click"]  # each click's `allowed`


def cut_grant(rig: Rig, site: Site) -> tuple[Any, str, str]:
    """A held answer granted on a server that dies right after the granted save left."""
    app = rig.boot(SendLauncher(site, cut_at=2))  # run 1 holds the answer, run 2 is the grant's
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    with pytest.raises(Exception, match=r"."):
        app.call("POST", f"/api/captures/{rid}/grant", {})
    assert site.writes == [SAVE] and held(app, did)["released"] == []
    return app, did, rid


@CUT
@pytest.mark.parametrize("op", ["grant", "deny"])
def test_a_decision_cut_before_any_write_left_sends_it_once_on_replay(rig: Rig, op: str) -> None:
    site = Site()
    app = rig.boot(SendLauncher(site))
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    jobs = app.httpd.deps.jobs
    assert jobs is not None
    with pytest.raises(SystemExit):
        jobs.run(op, did, {"rid": rid, "body": {}}, lambda: (_ for _ in ()).throw(SystemExit))
    rig.restart(app, SendLauncher(site))
    assert site.writes == ([SAVE] if op == "grant" else [])


@CUT
def test_a_grant_cut_after_its_write_left_never_sends_it_again(rig: Rig) -> None:
    site = Site()
    app, did, rid = cut_grant(rig, site)
    launcher = SendLauncher(site)
    fresh = rig.restart(app, launcher)
    assert site.writes == [SAVE], "kuhy granted one write: the replay must not send it again"
    assert all(SAVE not in allowed for page in launcher.pages for allowed in clicks(page))
    (out,) = held(fresh, did)["released"]
    assert "](assets/" in out["replacement"] and held(fresh, did)["held"] == []
    assert rig.jobs("grant")[0]["status"] == "done"
    assert fresh.httpd.captures.pending.record(rid)["sent"] == [list(SAVE)]


@CUT
def test_a_walk_that_needs_the_spent_write_asks_kuhy_again(rig: Rig) -> None:
    """The failed save leaves the page broken: Claude's fix runs the walk again, which cards the write."""
    site = Site()
    app, did, rid = cut_grant(rig, site)
    fresh = rig.restart(app, SendLauncher(site, broken=True))
    assert site.writes == [SAVE]
    view = held(fresh, did)
    (card,) = view["held"][0]["requests"]
    assert view["released"] == [] and card["id"] != rid and card["status"] == "pending"
    assert card["blocked"] == [list(SAVE)]
    assert fresh.call("POST", f"/api/captures/{card['id']}/grant", {})[0] == 200
    assert site.writes == [SAVE, SAVE]  # a second grant, a second write: one each
    assert len(held(fresh, did)["released"]) == 1


def test_a_spent_write_is_journaled_durably_and_comes_back_with_the_resumed_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[int] = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    store = PendingStore(tmp_path)
    store.dir.mkdir()
    spec = parse({"url": URL, "name": "dns-1", "steps": [{"click": "Zapisz"}], "highlight": "Zapisz"})
    rec: Any = store.create(
        "d1", spec, step=0, reason="request", detail="", blocked=[SAVE], grants=[], shot=b"x"
    )
    assert store.claim(rec["id"]).sent == ()
    store.spend(rec["id"], SAVE)
    assert len(synced) >= 2  # the record's file, then its directory
    assert store.claim(rec["id"], resume=True).sent == (SAVE,)
    assert store.record(rec["id"])["sent"] == [list(SAVE)]


def test_journaling_only_covers_its_own_block() -> None:
    seen: list[tuple[str, str]] = []
    spent.note(SAVE)  # outside a decision's run: nothing to journal
    with spent.journaling(seen.append):
        spent.note(SAVE)
    spent.note(SAVE)
    assert seen == [SAVE]


def test_without_drops_spent_pairs_and_keeps_the_rest() -> None:
    other = ("POST", f"{URL}api/pay")
    grant = Grant(0, click=True, requests=(SAVE, other))
    assert spent.without(grant, (SAVE,)) == Grant(0, click=True, requests=(other,))
    assert spent.without(grant, ()) is grant
