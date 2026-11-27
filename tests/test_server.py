"""HTTP API over the store and the adapters (fake Claude, fake Gmail)."""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from draft_studio import server as srv
from draft_studio.claude_fake import FakeClaude
from tests.conftest import App


def test_static_and_version(app: App) -> None:
    assert app.call("GET", "/") == (200, "<html>list abc123</html>")
    assert app.call("GET", "/d/?id=x")[1] == "<html>editor</html>"
    assert app.call("GET", "/app.js")[0] == 200
    assert app.call("GET", "/nope.js")[0] == 404
    assert app.call("GET", "/../secret")[0] == 404
    assert app.call("GET", "/version") == (200, {"build": "abc123"})


def test_host_and_origin_checks(app: App) -> None:
    assert app.call("GET", "/version", headers={"Host": "evil.com"})[0] == 403
    status, _ = app.call("POST", "/api/drafts", {"kind": "text"}, headers={"Origin": "http://evil.com"})
    assert status == 403
    port = app.httpd.server_address[1]
    ok = app.call("POST", "/api/drafts", {"kind": "text"}, headers={"Origin": f"http://localhost:{port}"})
    assert ok[0] == 200


def test_locales(app: App) -> None:
    assert app.call("GET", "/api/locales") == (200, {"locales": ["en", "pl"]})
    assert app.call("GET", "/locales/pl.json") == (200, {"dir": "ltr"})
    assert app.call("GET", "/locales/zz.json")[0] == 404


def test_draft_crud_and_autosave(app: App) -> None:
    _, mail = app.call("POST", "/api/drafts", {"kind": "mail"})
    _, text = app.call("POST", "/api/drafts", {"kind": "text"})
    assert app.call("POST", "/api/drafts", {"kind": "x"})[0] == 400
    assert app.call("POST", "/api/drafts", [1])[0] == 400
    _, listing = app.call("GET", "/api/drafts")
    assert {d["id"] for d in listing["drafts"]} == {mail["id"], text["id"]}
    _, saved = app.call("PUT", f"/api/drafts/{mail['id']}", {"text": "hi", "claude": [], "to": "a@b.c"})
    assert saved["text"] == "hi"
    assert app.call("PUT", f"/api/drafts/{mail['id']}", {"claude": [[2, 1]]})[0] == 400
    _, state = app.call("GET", f"/api/drafts/{mail['id']}")
    assert state["meta"]["to"] == "a@b.c"
    _, hist = app.call("GET", f"/api/drafts/{mail['id']}/history")
    assert len(hist["versions"]) == 2
    _, restored = app.call("POST", f"/api/drafts/{mail['id']}/restore", {"v": 0})
    assert restored["text"] == ""
    assert app.call("POST", f"/api/drafts/{mail['id']}/restore", {"v": 77})[0] == 404
    assert app.call("DELETE", f"/api/drafts/{text['id']}")[0] == 200
    assert app.call("GET", f"/api/drafts/{text['id']}")[0] == 404
    assert app.call("GET", "/api/nothing")[0] == 404
    assert app.call("PATCH", "/api/drafts")[0] in (404, 405, 501)


def test_bad_json_body(app: App) -> None:
    req = urllib.request.Request(app.base + "/api/drafts", data=b"{oops", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 400


def test_intake_rounds_then_draft(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "mail"})
    did = d["id"]
    _, r1 = app.call("POST", f"/api/drafts/{did}/brief", {"brief": "Napisz maila [2rounds]"})
    assert r1["questions"] and r1["state"]["meta"]["lang"] == "pl"
    _, r2 = app.call("POST", f"/api/drafts/{did}/intake", {"answers": ["Ania", "ciepło"]})
    assert len(r2["questions"]) == 1
    _, r3 = app.call("POST", f"/api/drafts/{did}/intake", {"answers": ["piątek"]})
    assert r3["questions"] == []
    state = r3["state"]
    assert "Ania" in state["text"]
    assert state["claude"] == [[0, len(state["text"])]]
    assert state["meta"]["subject"].startswith("[pl]")
    assert state["meta"]["phase"] == "editing"
    assert app.call("POST", f"/api/drafts/{did}/intake", {"answers": [1]})[0] == 400


def test_intake_keeps_your_subject(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "mail"})
    app.call("PUT", f"/api/drafts/{d['id']}", {"subject": "Mine", "subjectAuthor": "you"})
    _, r = app.call("POST", f"/api/drafts/{d['id']}/brief", {"brief": "hello [noq]"})
    assert r["state"]["meta"]["subject"] == "Mine"
    assert r["state"]["text"].startswith("Draft for")


def test_intake_round_cap(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeClaude, "intake", lambda self, ctx: {"lang": "en", "questions": ["again?"]})
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    _, r = app.call("POST", f"/api/drafts/{did}/brief", {"brief": "x"})
    for _ in range(10):
        if not r["questions"]:
            break
        _, r = app.call("POST", f"/api/drafts/{did}/intake", {"answers": ["a"]})
    assert r["questions"] == []
    assert r["state"]["text"]


def test_ask_and_propose(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    app.call("PUT", f"/api/drafts/{did}", {"text": "one two three", "claude": [], "lang": "pl"})
    body = {"text": "one two three", "from": 0, "to": 3, "question": "why?"}
    _, ask = app.call("POST", f"/api/drafts/{did}/ask", body)
    assert ask["answer"].startswith("[pl]")
    _, state = app.call("GET", f"/api/drafts/{did}")
    assert state["text"] == "one two three"
    assert state["meta"]["asks"][0]["question"] == "why?"
    _, prop = app.call("POST", f"/api/drafts/{did}/propose", {"text": "one two three", "from": 0, "to": 13})
    assert prop == {"replacement": "one 2 three better", "from": 0, "to": 13, "stripped": []}
    _, prop2 = app.call(
        "POST",
        f"/api/drafts/{did}/propose",
        {"text": "one two three", "from": 0, "to": 3, "instruction": "shout"},
    )
    assert prop2["replacement"] == "ONE"
    assert app.call("POST", f"/api/drafts/{did}/propose", {"text": "abc", "from": 2, "to": 9})[0] == 400
    assert app.call("POST", f"/api/drafts/{did}/ask", {"text": "abc", "from": 0, "to": 1})[0] == 400


def test_claude_failure_is_502_and_text_intact(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    app.call("PUT", f"/api/drafts/{did}", {"text": "keep", "claude": []})
    status, err = app.call(
        "POST", f"/api/drafts/{did}/propose", {"text": "keep", "from": 0, "to": 4, "instruction": "[fail]"}
    )
    assert status == 502 and err["error"] == "claude"
    assert app.call("POST", f"/api/drafts/{did}/brief", {"brief": "[fail]"})[0] == 502
    assert app.call("GET", f"/api/drafts/{did}")[1]["text"] == "keep"


def test_client_abort_is_swallowed(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Any, data: bytes) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(srv.Handler, "_write", broken)
    with pytest.raises((urllib.error.URLError, OSError, http.client.HTTPException)):
        urllib.request.urlopen(app.base + "/version", timeout=2).read()


def test_main_parses_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class FakeHttpd:
        def serve_forever(self) -> None:
            seen["served"] = True

    def fake_make(deps: srv.Deps, port: int) -> FakeHttpd:
        seen["port"] = port
        seen["deps"] = deps
        return FakeHttpd()

    monkeypatch.setattr(srv, "make_server", fake_make)
    monkeypatch.setenv("DRAFT_STUDIO_DATA", str(tmp_path / "d"))
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE", "fake")
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "fake")
    srv.main(["--port", "9999", "--build", "zz"])
    assert seen["port"] == 9999 and seen["served"] and seen["deps"].build == "zz"
    srv.main([])
    assert seen["port"] == srv.DEFAULT_PORT


def test_validation_and_odd_routes(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    assert (
        app.call("POST", f"/api/drafts/{did}/ask", {"text": 1, "from": 0, "to": 0, "question": "q"})[0] == 400
    )
    assert app.call("POST", f"/api/drafts/{did}/brief", {"brief": "  "})[0] == 400
    body = {"text": "ab", "from": 0, "to": 1, "instruction": 5}
    assert app.call("POST", f"/api/drafts/{did}/propose", body)[0] == 400
    assert app.call("POST", f"/api/drafts/{did}/ask", [1])[0] == 400
    assert app.call("PUT", f"/api/drafts/{did}", [1])[0] == 400
    assert app.call("POST", f"/api/drafts/{did}/nothing", {})[0] == 404
    assert app.call("PUT", "/api/drafts", {})[0] == 404
    assert app.call("PATCH", f"/api/drafts/{did}")[0] in (404, 501)
    assert app.call("POST", "/version", {})[0] == 404
    assert app.call("POST", f"/api/drafts/{did}", {})[0] == 404
    assert app.call("GET", f"/api/drafts/{did}/ask")[0] == 404


def test_fewer_answers_than_questions(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    app.call("POST", f"/api/drafts/{did}/brief", {"brief": "x"})
    _, r = app.call("POST", f"/api/drafts/{did}/intake", {"answers": ["only one"]})
    assert r["questions"] == []
    answers = [i["a"] for i in r["state"]["meta"]["intake"]]
    assert answers == ["only one", ""]


# -- image assets ---------------------------------------------------------------
