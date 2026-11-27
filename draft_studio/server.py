"""Loopback HTTP server: the built Astro pages plus the /api/* JSON routes.

Binds 127.0.0.1 only and checks Host/Origin so another site in the browser
(DNS rebinding, cross-origin POST) cannot drive Claude or read drafts.
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from draft_studio import approval
from draft_studio.claude import MAX_INTAKE_ROUNDS, Claude, ClaudeError, Context, make_claude
from draft_studio.gmail import Gmail, GmailError, make_gmail
from draft_studio.store import (
    MAX_ASSET_BYTES,
    AssetError,
    AssetNotFound,
    DraftNotFound,
    Store,
    default_data_dir,
)

DEFAULT_PORT = 8794
REPO = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("draft_studio")
_ROUTE = re.compile(r"^/api/drafts/([^/]+)(?:/([a-z]+))?$")
_LOCALE = re.compile(r"^/locales/([A-Za-z-]{2,16})\.json$")
# The path is matched undecoded, so %2e%2e or %2f never turn into a traversal.
_ASSETS = re.compile(r"^/api/drafts/([^/]+)/assets(?:/([^/]+))?$")
_CLOSE = {"Connection": "close"}
# Stored names carry a content hash, so a name never changes meaning: cache it for good.
_ASSET_HEADERS = {
    "Cache-Control": "private, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}

Json = dict[str, Any]


class ApiError(Exception):
    """An error with an HTTP status and a short machine code (shown as an icon)."""

    def __init__(self, status: int, code: str, close: bool = False) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        # close: the request body was left unread, so the keep-alive connection must end.
        self.headers = _CLOSE if close else {}


@dataclass
class Deps:
    """Everything a request handler needs."""

    store: Store
    claude: Claude
    gmail: Gmail
    dist: Path
    locales: Path
    build: str


def _span(body: Json) -> tuple[str, int, int]:
    text, start, end = body.get("text"), body.get("from"), body.get("to")
    if not (isinstance(text, str) and isinstance(start, int) and isinstance(end, int)):
        raise ApiError(400, "bad-span")
    if not 0 <= start <= end <= len(text):
        raise ApiError(400, "bad-span")
    return text, start, end


def _context(state: Json, text: str | None = None, **kw: Any) -> Context:
    meta = state["meta"]
    return Context(
        kind=meta["kind"],
        lang=meta["lang"],
        brief=meta["brief"],
        intake=[item for item in meta["intake"] if item.get("a") is not None],
        text=state["text"] if text is None else text,
        subject=meta["subject"],
        **kw,
    )


class Api:
    """Route bodies, independent of the HTTP plumbing."""

    def __init__(self, deps: Deps) -> None:
        self.d = deps

    def _next_step(self, draft_id: str) -> Json:
        """Ask the next intake round, or write the first draft when Claude is ready."""
        state = self.d.store.get(draft_id)
        rounds = len({item["round"] for item in state["meta"]["intake"]})
        result: Json = {"lang": state["meta"]["lang"], "questions": []}
        if rounds < MAX_INTAKE_ROUNDS:
            result = self.d.claude.intake(_context(state))
        if not state["meta"]["lang"]:
            state = self.d.store.save(draft_id, {"lang": result["lang"]}, kind="lang")
        if result["questions"]:
            pending = [{"q": q, "a": None, "round": rounds} for q in result["questions"]]
            state = self.d.store.save(
                draft_id, {"intake": state["meta"]["intake"] + pending, "phase": "intake"}, kind="intake"
            )
            return {"questions": result["questions"], "state": state}
        draft = self.d.claude.draft(_context(state))
        patch: Json = {"text": draft["text"], "phase": "editing"}
        patch["claude"] = [[0, len(draft["text"])]] if draft["text"] else []
        if state["meta"]["kind"] == "mail" and state["meta"]["subjectAuthor"] != "you" and draft["subject"]:
            patch.update(subject=draft["subject"], subjectAuthor="claude")
        state = self.d.store.save(draft_id, patch, kind="draft")
        return {"questions": [], "state": state}

    def brief(self, draft_id: str, body: Json) -> Json:
        """Store the brief and start intake."""
        brief = body.get("brief")
        if not isinstance(brief, str) or not brief.strip():
            raise ApiError(400, "bad-brief")
        self.d.store.save(draft_id, {"brief": brief, "intake": []}, kind="brief")
        return self._next_step(draft_id)

    def intake(self, draft_id: str, body: Json) -> Json:
        """Fill the open questions with answers, then take the next step."""
        answers = body.get("answers")
        if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
            raise ApiError(400, "bad-answers")
        state = self.d.store.get(draft_id)
        intake = [dict(item) for item in state["meta"]["intake"]]
        open_items = [item for item in intake if item["a"] is None]
        for item, answer in zip(open_items, answers, strict=False):
            item["a"] = answer
        for item in open_items[len(answers) :]:
            item["a"] = ""
        self.d.store.save(draft_id, {"intake": intake}, kind="intake")
        return self._next_step(draft_id)

    def ask(self, draft_id: str, body: Json) -> Json:
        """Answer a question about a span; saved in history, draft untouched."""
        text, start, end = _span(body)
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ApiError(400, "bad-question")
        state = self.d.store.get(draft_id)
        answer = self.d.claude.ask(_context(state, text, sel=(start, end), instruction=question))
        entry = {"from": start, "to": end, "span": text[start:end], "question": question, "answer": answer}
        return self.d.store.add_ask(draft_id, entry)

    def propose(self, draft_id: str, body: Json) -> Json:
        """A replacement for the span only. The client shows it as a diff to accept."""
        text, start, end = _span(body)
        instruction = body.get("instruction", "")
        if not isinstance(instruction, str):
            raise ApiError(400, "bad-instruction")
        state = self.d.store.get(draft_id)
        replacement = self.d.claude.rewrite(_context(state, text, sel=(start, end), instruction=instruction))
        self.d.store.log(
            draft_id,
            {
                "type": "proposal",
                "from": start,
                "to": end,
                "instruction": instruction,
                "replacement": replacement,
            },
        )
        return {"replacement": replacement, "from": start, "to": end}

    def gmail(self, draft_id: str) -> Json:
        """Create or update this mail's Gmail draft. Never sends."""
        state = self.d.store.get(draft_id)
        meta = state["meta"]
        if meta["kind"] != "mail":
            raise ApiError(400, "not-mail")
        out = self.d.gmail.upsert(meta["to"], meta["subject"], state["text"], meta["gmailDraftId"])
        self.d.store.update_meta(draft_id, gmailDraftId=out["id"])
        self.d.store.log(draft_id, {"type": "gmail", **out})
        return out


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
            self._json(403, {"error": "forbidden"}, _CLOSE)
            return
        path = urlsplit(self.path).path
        try:
            if asset := _ASSETS.match(path):
                self._assets(method, asset.group(1), asset.group(2))
            elif path.startswith("/api/"):
                self._json(200, self._api(method, path))
            elif method == "GET":
                self._static(path)
            else:
                raise ApiError(404, "not-found")
        except ApiError as err:
            self._json(err.status, {"error": err.code}, err.headers)
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
        api = Api(deps)
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
        match = _ROUTE.match(path)
        if not match:
            raise ApiError(404, "not-found")
        draft_id, action = match.groups()
        if action is None:
            if method == "GET":
                return store.get(draft_id)
            if method == "PUT":
                return self._save(store, draft_id)
            if method == "DELETE":
                store.delete(draft_id)
                return {"ok": True}
        if method == "GET" and action == "history":
            return {"versions": store.history(draft_id)}
        if method == "POST":
            body = self._body()
            if not isinstance(body, dict):
                raise ApiError(400, "bad-json")
            if action == "restore":
                try:
                    return store.restore(draft_id, int(body.get("v", -1)))
                except KeyError as exc:
                    raise ApiError(404, "no-version") from exc
            handlers = {"brief": api.brief, "intake": api.intake, "ask": api.ask, "propose": api.propose}
            if action in handlers:
                return handlers[action](draft_id, body)
            if action == "gmail":
                return api.gmail(draft_id)
            if action in ("approve", "unapprove"):
                return self._approval(store, draft_id, action, body)
        raise ApiError(404, "not-found")

    def _assets(self, method: str, draft_id: str, name: str | None) -> None:
        """Image routes: list, raw-bytes upload, and serving one stored image."""
        store = self.server.deps.store
        if name is None and method == "GET":
            self._json(200, {"assets": store.list_assets(draft_id)})
        elif name is None and method == "POST":
            self._json(200, self._upload(store, draft_id))
        elif name is not None and method == "GET":
            data, ctype = store.asset(draft_id, name)
            self._send(200, data, ctype, _ASSET_HEADERS)
        else:
            raise ApiError(404, "not-found", close=True)

    def _upload(self, store: Store, draft_id: str) -> Json:
        """Check type and declared size before reading the body; the store sniffs the bytes."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            raise ApiError(415, "bad-image", close=True)
        declared = self.headers.get("Content-Length") or ""
        if not declared.isdigit():
            raise ApiError(411, "no-length", close=True)
        if int(declared) > MAX_ASSET_BYTES:
            raise ApiError(413, "too-large", close=True)
        data = self.rfile.read(int(declared))
        raw_name = parse_qs(urlsplit(self.path).query).get("name", [""])[0]
        try:
            stored = store.add_asset(draft_id, raw_name, data)
        except AssetError as exc:
            raise ApiError(400, exc.code) from exc
        return {"name": stored, "md": f"![](assets/{stored})"}

    def _approval(self, store: Store, draft_id: str, action: str, body: Json) -> Json:
        """approve: body {"text": what the editor shows}; unapprove takes no fields."""
        if action == "unapprove":
            return approval.unapprove(store, draft_id)
        if not isinstance(body.get("text"), str):
            raise ApiError(400, "bad-text")
        try:
            return approval.approve(store, draft_id, body["text"])
        except approval.ApprovalError as exc:
            raise ApiError(409, exc.code) from exc

    def _save(self, store: Store, draft_id: str) -> Any:
        body = self._body()
        if not isinstance(body, dict):
            raise ApiError(400, "bad-json")
        try:
            return store.save(draft_id, body)
        except ValueError as exc:
            raise ApiError(400, "bad-save") from exc

    def _static(self, path: str) -> None:
        deps = self.server.deps
        locale = _LOCALE.match(path)
        if path == "/version":
            self._json(200, {"build": deps.build})
            return
        if locale:
            file = deps.locales / f"{locale.group(1)}.json"
        else:
            rel = path.lstrip("/")
            file = deps.dist / rel
            if path.endswith("/"):
                file = file / "index.html"
        root = deps.locales if locale else deps.dist
        try:
            file.resolve().relative_to(root.resolve())
        except ValueError:
            file = root / "__missing__"
        if not file.is_file():
            self._json(404, {"error": "not-found"})
            return
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        data = file.read_bytes()
        if file.suffix == ".html":
            data = data.replace(b"__BUILD__", deps.build.encode())
            ctype = "text/html; charset=utf-8"
        self._send(200, data, ctype)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, deps: Deps, port: int) -> None:
        self.deps = deps
        super().__init__(("127.0.0.1", port), Handler)


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
