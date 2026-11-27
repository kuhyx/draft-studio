"""Image routes: upload, list, serve, and the checks around them."""

from __future__ import annotations

import http.client
import json
from typing import Any

from draft_studio.store import MAX_ASSET_BYTES
from tests.conftest import App
from tests.imgdata import GIF, JPEG, SVG, png


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
