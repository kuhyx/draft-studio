"""Loopback HTTP server: the built Astro pages plus the /api/* JSON routes.

Binds 127.0.0.1 only and checks Host/Origin so another site in the browser
(DNS rebinding, cross-origin POST) cannot drive Claude or read drafts.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from draft_studio import feed, files, loginapi, proposalapi
from draft_studio.api import CLOSE, Api, ApiError, Deps
from draft_studio.capture import routes as capture_routes
from draft_studio.capture.service import CaptureService
from draft_studio.claude import ClaudeError
from draft_studio.claude_fake import make_claude
from draft_studio.gmail import GmailError, make_gmail
from draft_studio.shots import Held
from draft_studio.store import AssetNotFound, DraftNotFound, Store, default_data_dir

__all__ = ["DEFAULT_PORT", "Api", "ApiError", "Deps", "Handler", "main", "make_server"]

DEFAULT_PORT = 8794
REPO = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("draft_studio")
_ROUTE = re.compile(r"^/api/drafts/([^/]+)(?:/([a-z]+))?$")
# The path is matched undecoded, so %2e%2e or %2f never turn into a traversal.
_ASSETS = re.compile(r"^/api/drafts/([^/]+)/assets(?:/([^/]+))?$")
_EXPORT = re.compile(r"^/api/drafts/([^/]+)/export\.pdf$")
_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
_LOGIN = re.compile(r"^/api/drafts/([^/]+)/held/([^/]+)/login$")


class Handler(BaseHTTPRequestHandler):
    """Static files + JSON API. One instance per request."""

    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Route access logs to logging at debug level."""
        LOG.debug(format, *args)

    # -- plumbing --------------------------------------------------------
    def _write(self, data: bytes) -> None:
        self.wfile.write(data)

    def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in {"Cache-Control": "no-store", **(headers or {})}.items():
                self.send_header(key, value)
            self.end_headers()
            self._write(body)
        except (BrokenPipeError, ConnectionResetError):
            LOG.debug("client went away")
            self.close_connection = True

    def _json(self, status: int, obj: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _allowed_host(self) -> bool:
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlsplit(origin).netloc in hosts

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(400, "bad-json") from exc

    def _dispatch(self, method: str) -> None:
        if not self._allowed_host():
            # The body (maybe an upload) stays unread: close so it cannot leak into the next request.
            self._json(403, {"error": "forbidden"}, CLOSE)
            return
        path = urlsplit(self.path).path
        try:
            if asset := _ASSETS.match(path):
                self._assets(method, asset.group(1), asset.group(2))
            elif (pdf := _EXPORT.match(path)) and method == "GET":
                body, disposition = Api(self.server.deps, self.server.captures).export_pdf(pdf.group(1))
                self._send(200, body, "application/pdf", {"Content-Disposition": disposition})
            elif (login := _LOGIN.match(path)) and method == "POST":
                shots = Api(self.server.deps, self.server.captures).shots()
                self._json(*loginapi.handle(self.server.logins, shots, login[1], login[2], self._body()))
            elif capture_routes.matches(path):
                self._capture(method, path)
            elif path == "/api/events" and method == "GET":
                feed.stream(self, self.server.deps.store.feed)
            elif path.startswith("/api/"):
                self._json(202 if isinstance(out := self._api(method, path), Held) else 200, out)
            elif method == "GET":
                self._static(path)
            else:
                raise ApiError(404, "not-found")
        except ApiError as err:
            self._json(err.status, err.body, err.headers)
        except (DraftNotFound, AssetNotFound):
            self._json(404, {"error": "not-found"})
        except ClaudeError as err:
            LOG.warning("claude failed: %s", err)
            self._json(502, {"error": "claude"})
        except GmailError as err:
            LOG.warning("gmail failed: %s", err)
            self._json(502, {"error": "gmail"})

    def do_GET(self) -> None:
        """GET: pages, assets, locales, API reads."""
        self._dispatch("GET")

    def do_POST(self) -> None:
        """POST: API actions."""
        self._dispatch("POST")

    def do_PUT(self) -> None:
        """PUT: autosave."""
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        """DELETE: move a draft to trash."""
        self._dispatch("DELETE")

    # -- routes ------------------------------------------------------------
    def _api(self, method: str, path: str) -> Any:
        deps = self.server.deps
        store = deps.store
        api = Api(deps, self.server.captures)
        if path == "/api/locales" and method == "GET":
            return {"locales": sorted(p.stem for p in deps.locales.glob("*.json"))}
        if path == "/api/drafts":
            if method == "GET":
                return {"drafts": store.list_drafts()}
            if method == "POST":
                body = self._body()
                if not isinstance(body, dict) or body.get("kind") not in ("mail", "text"):
                    raise ApiError(400, "bad-kind")
                return store.create(body["kind"])
        if (res := proposalapi.ROUTE.match(path)) and method == "POST":
            return proposalapi.resolve(store, res[1], int(res[2]), res[3], self._body())
        match = _ROUTE.match(path)
        if not match:
            raise ApiError(404, "not-found")
        draft_id, action = match.groups()
        if action is None:
            if method == "GET":
                return store.get(draft_id)
            if method == "PUT":
                return api.save(draft_id, self._body())
            if method == "DELETE":
                store.delete(draft_id)
                return {"ok": True}
        if method == "GET" and action in ("history", "held"):
            return api.held(draft_id) if action == "held" else {"versions": store.history(draft_id)}
        if method == "POST":
            body = self._body()
            if not isinstance(body, dict):
                raise ApiError(400, "bad-json")
            handlers = {"brief": api.brief, "intake": api.intake, "ask": api.ask, "propose": api.propose}
            handlers.update(held=api.held, restore=api.restore)
            if action in handlers:
                return handlers[action](draft_id, body)
            if action == "gmail":
                return api.gmail(draft_id)
            if action in ("approve", "unapprove"):
                return api.approval(draft_id, action, body)
        raise ApiError(404, "not-found")

    def _assets(self, method: str, draft_id: str, name: str | None) -> None:
        """Image routes: list, raw-bytes upload, and serving one stored image."""
        store = self.server.deps.store
        if name is None and method == "GET":
            self._json(200, {"assets": store.list_assets(draft_id)})
        elif name is None and method == "POST":
            self._json(200, files.upload(store, draft_id, self.headers, self.rfile, self.path))
        elif name is not None and method == "GET":
            data, ctype = store.asset(draft_id, name)
            self._send(200, data, ctype, files.ASSET_HEADERS)
        else:
            raise ApiError(404, "not-found", close=True)

    def _capture(self, method: str, path: str) -> None:
        """Screenshot capture and its permission requests (draft_studio/capture)."""
        status, out = capture_routes.handle(self.server.captures, method, path, self._body)
        Api(self.server.deps, self.server.captures).after_capture(method, path, status, out)
        if isinstance(out, bytes):
            self._send(status, out, "image/png", files.ASSET_HEADERS)
        else:
            self._json(status, out)

    def _static(self, path: str) -> None:
        found = files.static_file(self.server.deps, path)
        if found is None:
            raise ApiError(404, "not-found")
        self._send(200, *found)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, deps: Deps, port: int) -> None:
        self.deps = deps
        self.captures = CaptureService(deps.store)
        self.logins = loginapi.LoginWindows(deps.store.root)
        super().__init__(("127.0.0.1", port), Handler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that hung up mid-request is debug noise; anything else keeps the traceback."""
        if isinstance(sys.exc_info()[1], _CLIENT_GONE):
            LOG.debug("client %s went away mid-request", client_address)
            return
        super().handle_error(request, client_address)


def make_server(deps: Deps, port: int) -> _Server:
    """A server bound to loopback only."""
    return _Server(deps, port)


def main(argv: list[str] | None = None) -> None:
    """Entry point: `python -m draft_studio [--port N] [--build ID]`."""
    parser = argparse.ArgumentParser(prog="draft_studio")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--build", default="")
    parser.add_argument("--dist", type=Path, default=REPO / "web" / "dist")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    data = default_data_dir()
    build_file = args.dist / "build-id"
    build = args.build or (build_file.read_text().strip() if build_file.is_file() else "dev")
    deps = Deps(
        store=Store(data),
        claude=make_claude(),
        gmail=make_gmail(data),
        dist=args.dist,
        locales=Path(os.environ.get("DRAFT_STUDIO_LOCALES") or REPO / "locales"),
        build=build,
    )
    make_server(deps, args.port).serve_forever()
