"""HTTP API over the store and the adapters (fake Claude, fake Gmail)."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio import server as srv
from draft_studio.claude import FakeClaude
from draft_studio.gmail import FakeGmail, GmailError
from draft_studio.store import MAX_ASSET_BYTES, Store
from tests.imgdata import GIF, JPEG, SVG, png


class App:
    def __init__(self, base: str, store: Store, gmail: FakeGmail, httpd: Any) -> None:
        self.base, self.store, self.gmail, self.httpd = base, store, gmail, httpd

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, json.loads(raw) if "json" in ctype else raw.decode()
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw)
            except json.JSONDecodeError:
                return err.code, raw.decode()


@pytest.fixture
def app(tmp_path: Path) -> Iterator[App]:
    dist = tmp_path / "dist"
    (dist / "d").mkdir(parents=True)
    (dist / "index.html").write_text("<html>list __BUILD__</html>")
    (dist / "d" / "index.html").write_text("<html>editor</html>")
    (dist / "app.js").write_text("console.log(1)")
    locales = tmp_path / "locales"
    locales.mkdir()
    (locales / "en.json").write_text('{"dir": "ltr"}')
    (locales / "pl.json").write_text('{"dir": "ltr"}')
    (locales / "README").write_text("x")
    store = Store(tmp_path / "data")
    gmail = FakeGmail(tmp_path / "data" / "gmail-fake.json")
    httpd = srv.make_server(
        srv.Deps(store=store, claude=FakeClaude(), gmail=gmail, dist=dist, locales=locales, build="abc123"),
        port=0,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield App(f"http://127.0.0.1:{port}", store, gmail, httpd)
    httpd.shutdown()
    httpd.server_close()


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
    assert prop == {"replacement": "one 2 three better", "from": 0, "to": 13}
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


def test_gmail_upsert_flow(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "mail"})
    did = d["id"]
    app.call("PUT", f"/api/drafts/{did}", {"text": "Body", "to": "a@b.c", "subject": "S"})
    _, g1 = app.call("POST", f"/api/drafts/{did}/gmail", {})
    assert g1["created"] is True
    app.call("PUT", f"/api/drafts/{did}", {"text": "Body 2"})
    _, g2 = app.call("POST", f"/api/drafts/{did}/gmail", {})
    assert g2 == {"id": g1["id"], "created": False}
    assert len(app.gmail.drafts()) == 1
    assert app.gmail.drafts()[g1["id"]]["body"] == "Body 2"
    app.gmail.delete(g1["id"])
    _, g3 = app.call("POST", f"/api/drafts/{did}/gmail", {})
    assert g3["created"] is True
    assert app.store.get(did)["meta"]["gmailDraftId"] == g3["id"]


def test_gmail_text_draft_refused_and_errors(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    _, t = app.call("POST", "/api/drafts", {"kind": "text"})
    assert app.call("POST", f"/api/drafts/{t['id']}/gmail", {})[0] == 400
    _, m = app.call("POST", "/api/drafts", {"kind": "mail"})

    def boom(*a: Any) -> None:
        raise GmailError("x")

    monkeypatch.setattr(FakeGmail, "upsert", boom)
    status, err = app.call("POST", f"/api/drafts/{m['id']}/gmail", {})
    assert status == 502 and err["error"] == "gmail"


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
def raw(
    app: App, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    """One request on its own connection; the path goes out byte for byte (no normalising)."""
    port = app.httpd.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def upload(app: App, did: str, name: str, data: bytes, ctype: str = "image/png") -> tuple[int, Any]:
    status, _, body = raw(app, "POST", f"/api/drafts/{did}/assets?name={name}", data, {"Content-Type": ctype})
    return status, json.loads(body)


def test_asset_upload_serve_list(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    status, out = upload(app, did, "shot%201.png", png())
    assert status == 200
    name = out["name"]
    assert name.startswith("shot-1-") and name.endswith(".png")
    assert out["md"] == f"![](assets/{name})"
    status, headers, body = raw(app, "GET", f"/api/drafts/{did}/assets/{name}")
    assert (status, body) == (200, png())
    assert headers["content-type"] == "image/png"
    assert headers["x-content-type-options"] == "nosniff"
    assert "max-age" in headers["cache-control"]
    assert app.call("GET", f"/api/drafts/{did}/assets") == (
        200,
        {"assets": [{"name": name, "size": len(png()), "type": "image/png"}]},
    )
    # The draft itself is untouched: no new version.
    assert app.call("GET", f"/api/drafts/{did}")[1]["meta"]["version"] == 0


def test_asset_dedupe_and_sniffed_type(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    first = upload(app, did, "a.png", png())[1]["name"]
    assert upload(app, did, "b.png", png())[1]["name"] == first
    status, out = upload(app, did, "lies.png", JPEG, "image/png")
    assert status == 200 and out["name"].endswith(".jpg")
    _, headers, _ = raw(app, "GET", f"/api/drafts/{did}/assets/{out['name']}")
    assert headers["content-type"] == "image/jpeg"
    assert upload(app, did, "", GIF, "image/gif")[1]["name"].startswith("image-")
    assert len(app.call("GET", f"/api/drafts/{did}/assets")[1]["assets"]) == 3


def test_asset_bad_type_and_size(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    assert upload(app, did, "x.svg", SVG, "image/svg+xml") == (400, {"error": "bad-image"})
    assert upload(app, did, "x.png", png(), "application/json") == (415, {"error": "bad-image"})
    assert upload(app, did, "x.png", png(), "text/plain") == (415, {"error": "bad-image"})
    # Declared too large: refused before the body is read, and the connection is closed.
    status, headers, body = raw(
        app,
        "POST",
        f"/api/drafts/{did}/assets?name=big.png",
        png(),
        {"Content-Type": "image/png", "Content-Length": str(MAX_ASSET_BYTES + 1)},
    )
    assert (status, json.loads(body)) == (413, {"error": "too-large"})
    assert headers["connection"] == "close"
    # Exactly at the cap is fine.
    big = png() + b"\x00" * (MAX_ASSET_BYTES - len(png()))
    assert upload(app, did, "edge.png", big)[0] == 200
    assert upload(app, did, "x.png", b"", "image/png") == (400, {"error": "bad-image"})
    assert len(app.call("GET", f"/api/drafts/{did}/assets")[1]["assets"]) == 1


def test_asset_needs_length(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    port = app.httpd.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("POST", f"/api/drafts/{d['id']}/assets?name=a.png")
    conn.putheader("Content-Type", "image/png")
    conn.endheaders()
    resp = conn.getresponse()
    assert (resp.status, json.loads(resp.read())) == (411, {"error": "no-length"})
    conn.close()


def test_asset_traversal_and_unknowns(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    app.call("PUT", f"/api/drafts/{did}", {"text": "secret"})
    name = upload(app, did, "a.png", png())[1]["name"]
    for path in [
        f"/api/drafts/{did}/assets/../draft.md",
        f"/api/drafts/{did}/assets/..%2Fdraft.md",
        f"/api/drafts/{did}/assets/%2e%2e%2fmeta.json",
        f"/api/drafts/{did}/assets/..",
        f"/api/drafts/{did}/assets/.hidden.png",
        f"/api/drafts/{did}/assets/missing.png",
        f"/api/drafts/{did}/assets/{name}/x",
        f"/api/drafts/..%2f..%2fetc/assets/{name}",
        f"/api/drafts/000000000000/assets/{name}",
        "/api/drafts/000000000000/assets",
    ]:
        status, _, body = raw(app, "GET", path)
        assert status == 404, path
        assert b"secret" not in body
    status, _ = upload(app, "000000000000", "a.png", png())
    assert status == 404
    assert raw(app, "DELETE", f"/api/drafts/{did}/assets/{name}")[0] == 404
    assert (
        raw(app, "POST", f"/api/drafts/{did}/assets/{name}", png(), {"Content-Type": "image/png"})[0] == 404
    )
    assert raw(app, "PUT", f"/api/drafts/{did}/assets", b"{}")[0] == 404


def test_asset_routes_check_host_and_origin(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    name = upload(app, did, "a.png", png())[1]["name"]
    assert raw(app, "GET", f"/api/drafts/{did}/assets/{name}", headers={"Host": "evil.com"})[0] == 403
    assert raw(app, "GET", f"/api/drafts/{did}/assets", headers={"Host": "evil.com"})[0] == 403
    status, _, _ = raw(
        app,
        "POST",
        f"/api/drafts/{did}/assets?name=b.png",
        png(rgb=(0, 0, 0)),
        {"Content-Type": "image/png", "Origin": "http://evil.com"},
    )
    assert status == 403
    assert len(app.call("GET", f"/api/drafts/{did}/assets")[1]["assets"]) == 1


def test_trashed_draft_takes_its_assets(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    name = upload(app, did, "a.png", png())[1]["name"]
    app.call("DELETE", f"/api/drafts/{did}")
    assert raw(app, "GET", f"/api/drafts/{did}/assets/{name}")[0] == 404
    (trashed,) = (app.store.root / "trash").iterdir()
    assert (trashed / "assets" / name).read_bytes() == png()
