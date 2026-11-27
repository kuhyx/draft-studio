"""Proposals live on the server (draft_studio/proposalapi.py): landing, accept, reject, revise, restart, feed."""

from __future__ import annotations

from typing import Any

from draft_studio.proposals import width
from draft_studio.store import Store
from tests.conftest import App
from tests.test_held import grant, held, hold, saving

TEXT = "alpha beta gamma delta"


def draft(app: App, text: str = TEXT) -> str:
    did = str(app.store.create("text")["id"])
    app.store.save(did, {"text": text, "phase": "editing"})
    return did


def ask(app: App, did: str, needle: str, **extra: Any) -> dict[str, Any]:
    text = app.store.get(did)["text"]
    start = width(text[: text.index(needle)])  # the editor counts UTF-16 units
    body = {"text": text, "from": start, "to": start + width(needle), "instruction": "", **extra}
    status, out = app.call("POST", f"/api/drafts/{did}/propose", body)
    assert status == 200, out
    return dict(out)


def proposals(app: App, did: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = app.call("GET", f"/api/drafts/{did}")[1]["proposals"]
    return out


def resolve(app: App, did: str, pid: int, action: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    return app.call("POST", f"/api/drafts/{did}/proposals/{pid}/{action}", body or {})


def test_a_proposal_is_stored_with_the_draft_and_survives_a_restart(app: App) -> None:
    did = draft(app)
    out = ask(app, did, "alpha beta gamma")
    assert (out["outcome"], out["proposal"]) == ("ok", 1)
    (p,) = proposals(app, did)
    assert (p["id"], p["from"], p["to"], p["replacement"], p["rev"]) == (1, 0, 16, "alpha 2 gamma better", 1)
    assert app.store.get(did)["text"] == TEXT  # nothing applied by asking
    again = Store(app.store.root)  # a restarted server reads the same folder
    assert again.get(did)["proposals"] == [p]


def test_accept_applies_the_replacement_with_claude_tint_and_tells_the_feed(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    seq, version = app.store.feed.seq, app.store.get(did)["meta"]["version"]
    status, state = resolve(app, did, 1, "accept", {"rev": 1})
    assert status == 200
    assert state["text"] == "alpha 2 gamma better delta" and state["proposals"] == []
    assert state["claude"] == [[0, 20]] and state["gone"] == {"1": "accepted"}
    assert state["meta"]["version"] == version + 1 and app.store.feed.seq > seq
    assert app.store.history(did)[-1]["type"] == "accept"


def test_accept_some_hunks_keeps_the_proposal_for_the_rest(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    status, state = resolve(app, did, 1, "accept", {"rev": 1, "text": "alpha 2 gamma"})  # the first hunk only
    assert status == 200 and state["text"] == "alpha 2 gamma delta" and state["claude"] == [[0, 13]]
    (left,) = state["proposals"]
    assert (left["from"], left["to"], left["rev"]) == (0, 13, 2)
    assert left["replacement"] == "alpha 2 gamma better"
    _, state = resolve(app, did, 1, "accept", {"rev": 2})
    assert state["text"] == "alpha 2 gamma better delta" and state["proposals"] == []


def test_accept_of_text_equal_to_the_span_changes_nothing(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    seq = app.store.feed.seq
    status, state = resolve(app, did, 1, "accept", {"text": "alpha beta gamma"})
    assert status == 200 and state["text"] == TEXT and len(state["proposals"]) == 1
    assert app.store.feed.seq == seq


def test_accept_moves_the_later_proposals_along(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    ask(app, did, "delta")
    first, second = proposals(app, did)
    _, state = resolve(app, did, first["id"], "accept")
    (moved,) = state["proposals"]
    assert moved["id"] == second["id"]
    assert state["text"][moved["from"] : moved["to"]] == "delta"


def test_accept_counts_utf16_units_like_the_editor(app: App) -> None:
    did = draft(app, "😀 alpha beta gamma delta")
    out = ask(app, did, "alpha beta gamma")
    assert out["outcome"] == "ok" and proposals(app, did)[0]["from"] == 3  # the emoji is two units
    _, state = resolve(app, did, 1, "accept")
    assert state["text"] == "😀 alpha 2 gamma better delta" and state["claude"] == [[3, 23]]


def test_reject_drops_it_or_only_the_hunks_left_out(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    status, state = resolve(
        app, did, 1, "reject", {"replacement": "alpha beta gamma"}
    )  # as it is: no hunk left
    assert status == 200 and state["proposals"] == [] and state["gone"] == {"1": "rejected"}
    ask(app, did, "alpha beta gamma", instruction="shout")
    (p,) = proposals(app, did)
    _, state = resolve(app, did, p["id"], "reject", {"replacement": "ALPHA beta gamma"})
    assert state["proposals"][0]["replacement"] == "ALPHA beta gamma" and state["text"] == TEXT
    _, state = resolve(app, did, p["id"], "reject")
    assert state["proposals"] == [] and state["text"] == TEXT


def test_a_resolve_with_an_old_rev_is_a_409_with_the_current_state(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    resolve(app, did, 1, "reject", {"replacement": "ALPHA beta gamma"})  # rev 2 now
    status, body = resolve(app, did, 1, "accept", {"rev": 1})
    assert (status, body["error"]) == (409, "stale")
    assert body["state"]["proposals"][0]["rev"] == 2 and body["state"]["text"] == TEXT


def test_resolve_errors(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    assert resolve(app, did, 7, "accept")[0] == 404
    assert resolve(app, did, 1, "accept", {"text": 5})[0] == 400
    assert resolve(app, did, 1, "reject", {"replacement": ["x"]})[0] == 400
    assert app.call("POST", f"/api/drafts/{did}/proposals/1/accept", [1])[0] == 400
    assert app.call("GET", f"/api/drafts/{did}/proposals/1/accept")[0] == 404
    assert resolve(app, "0" * 12, 1, "accept")[0] == 404


def test_an_edit_in_a_span_drops_the_proposal_and_one_before_it_shifts_the_other(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    ask(app, did, "delta")
    base = app.store.get(did)["meta"]["version"]
    status, state = app.call(
        "PUT", f"/api/drafts/{did}", {"text": TEXT.replace("beta", "bXta"), "base": base}
    )
    assert status == 200
    (kept,) = state["proposals"]
    assert state["text"][kept["from"] : kept["to"]] == "delta" and state["gone"] == {"1": "stale"}
    _, state = app.call("PUT", f"/api/drafts/{did}", {"text": "NEW " + state["text"]})
    assert state["proposals"][0]["from"] == kept["from"] + 4


def test_an_answer_for_a_span_edited_meanwhile_is_dropped_as_stale(app: App) -> None:
    did = draft(app)
    text = app.store.get(did)["text"]
    app.store.save(did, {"text": text.replace("beta", "bXta")})  # saved while Claude worked
    body = {"text": text, "from": 0, "to": 16, "instruction": ""}
    status, out = app.call("POST", f"/api/drafts/{did}/propose", body)
    assert (status, out["outcome"], out["proposal"]) == (200, "stale", None)
    assert proposals(app, did) == []
    body = {"text": text, "from": 17, "to": 22, "instruction": ""}  # an edit elsewhere only moves it
    assert app.call("POST", f"/api/drafts/{did}/propose", body)[1]["outcome"] == "ok"


def test_a_revision_keeps_its_id_and_a_vanished_or_overlapped_one_is_dropped(app: App) -> None:
    did = draft(app)
    ask(app, did, "alpha beta gamma")
    out = ask(app, did, "alpha beta gamma", instruction="shout", revises=[1])
    assert (out["outcome"], out["proposal"]) == ("ok", 1)
    (p,) = proposals(app, did)
    assert p["id"] == 1 and p["rev"] == 2 and p["replacement"] == "ALPHA BETA GAMMA"
    assert ask(app, did, "alpha beta gamma", revises=[9])["outcome"] == "gone"
    assert ask(app, did, "gamma delta")["outcome"] == "overlap"
    assert len(proposals(app, did)) == 1


def test_a_bad_revises_is_a_400(app: App) -> None:
    did = draft(app)
    body = {"text": TEXT, "from": 0, "to": 5, "revises": ["1"]}
    assert app.call("POST", f"/api/drafts/{did}/propose", body)[0] == 400
    assert app.call("POST", f"/api/drafts/{did}/propose", {**body, "revises": 1})[0] == 400


def test_a_released_held_answer_lands_once_whoever_polls(app: App) -> None:
    saving(app)
    did, h = hold(app)
    assert proposals(app, did) == []  # held: nothing on the server yet
    seq = app.store.feed.seq
    held(app, did)
    held(app, did)
    assert app.store.feed.seq == seq  # polling an unchanged hold tells nobody
    assert grant(app, h["requests"][0]["id"])[0] == 200
    assert app.store.feed.seq > seq  # the release does
    (p,) = proposals(app, did)
    assert "assets/" in p["replacement"]
    (rel,) = held(app, did)["released"]
    assert (rel["outcome"], rel["proposal"]) == ("ok", p["id"])
    held(app, did)
    assert len(proposals(app, did)) == 1
