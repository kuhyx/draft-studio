"""JSON route bodies, independent of the HTTP plumbing in server.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from draft_studio import approval, export, mailhtml
from draft_studio.apiutil import CLOSE, ApiError, Json, _context, _span
from draft_studio.capture import routes as capture_routes
from draft_studio.capture.service import CaptureService
from draft_studio.claude import MAX_INTAKE_ROUNDS, Claude, Context
from draft_studio.held import HeldNotFound
from draft_studio.heldapi import BadHeld, HeldApi
from draft_studio.mail import Gmail, MailError
from draft_studio.shotout import stored
from draft_studio.shots import Shots
from draft_studio.store import StaleBase, Store

__all__ = ["CLOSE", "Api", "ApiError", "Deps"]


@dataclass
class Deps:
    """Everything a request handler needs."""

    store: Store
    claude: Claude
    gmail: Gmail
    dist: Path
    locales: Path
    build: str


class Api:
    """Route bodies, independent of the HTTP plumbing."""

    def __init__(self, deps: Deps, captures: CaptureService) -> None:
        self.d = deps
        self.captures = captures

    def _ctx(self, draft_id: str, state: Json, text: str | None = None, **kw: Any) -> Context:
        """The context of one call, with the draft's stored image names (the only ones it may link)."""
        return _context(state, text, assets=stored(self.d.store, draft_id), **kw)

    def context_for(self, rec: Json) -> Context:
        """The context an answer was made in (shots.py asks Claude to fix a capture in it)."""
        state, ask = self.d.store.get(rec["draftId"]), rec["ask"]
        if rec["kind"] == "draft":
            return self._ctx(rec["draftId"], state)
        sel = (ask["from"], ask["to"])
        kw = {"sel": sel, "instruction": ask["instruction"], "previous": ask["previous"]}
        return self._ctx(rec["draftId"], state, ask["text"], **kw)

    def shots(self) -> Shots:
        """The capture pipeline of this server (server.py passes its one CaptureService)."""
        return Shots(self.d.store, self.d.claude, self.captures, self.context_for)

    def held(self, draft_id: str, body: Json | None = None) -> Json:
        """GET (body None) or POST /api/drafts/<id>/held (heldapi.py)."""
        api = HeldApi(self.shots())
        if body is None:
            return api.listing(draft_id)
        try:
            return api.post(draft_id, body)
        except BadHeld as exc:
            raise ApiError(400, "bad-held") from exc
        except HeldNotFound as exc:
            raise ApiError(404, "not-found") from exc

    def after_capture(self, method: str, path: str, status: int, out: Json | bytes) -> None:
        """A grant or deny of a capture request may resolve a held answer."""
        rid = capture_routes.decision(method, path)
        if rid and isinstance(out, dict):
            HeldApi(self.shots()).decided(rid, status, out)

    def _next_step(self, draft_id: str) -> Json:
        """Ask the next intake round, or write the first draft when Claude is ready."""
        state = self.d.store.get(draft_id)
        rounds = len({item["round"] for item in state["meta"]["intake"]})
        result: Json = {"lang": state["meta"]["lang"], "questions": []}
        if rounds < MAX_INTAKE_ROUNDS:
            result = self.d.claude.intake(self._ctx(draft_id, state))
        if not state["meta"]["lang"]:
            state = self.d.store.save(draft_id, {"lang": result["lang"]}, kind="lang")
        if result["questions"]:
            pending = [{"q": q, "a": None, "round": rounds} for q in result["questions"]]
            state = self.d.store.save(
                draft_id, {"intake": state["meta"]["intake"] + pending, "phase": "intake"}, kind="intake"
            )
            return {"questions": result["questions"], "state": state, "stripped": []}
        draft = self.d.claude.draft(self._ctx(draft_id, state))
        meta = state["meta"]
        kuhy = "\n".join([meta["brief"], state["text"], *(item["a"] or "" for item in meta["intake"])])
        rec = {"draftId": draft_id, "kind": "draft", "ask": {}, "kuhy": kuhy, "sources": kuhy}
        rec["answer"] = {"text": draft["text"], "subject": draft["subject"]}
        return self.shots().answer(rec, draft["captures"])

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
        answer = self.d.claude.ask(self._ctx(draft_id, state, text, sel=(start, end), instruction=question))
        entry = {"from": start, "to": end, "span": text[start:end], "question": question, "answer": answer}
        return self.d.store.add_ask(draft_id, entry)

    def propose(self, draft_id: str, body: Json) -> Json:
        """A replacement for the span only, or a revision of its pending one. The client shows a diff."""
        text, start, end = _span(body)
        instruction = body.get("instruction", "")
        if not isinstance(instruction, str):
            raise ApiError(400, "bad-instruction")
        # previous: the pending replacement kuhy commented on; Claude revises it.
        previous = body.get("previous", "")
        if not isinstance(previous, str):
            raise ApiError(400, "bad-previous")
        state = self.d.store.get(draft_id)
        out = self.d.claude.rewrite(
            self._ctx(draft_id, state, text, sel=(start, end), instruction=instruction, previous=previous)
        )
        ask = {"text": text, "from": start, "to": end, "instruction": instruction, "previous": previous}
        meta = state["meta"]
        # kuhy's instruction is his own words too: a walkthrough's start URL is often only there.
        sources = "\n".join(
            [meta["brief"], text, previous, instruction, *(i["a"] or "" for i in meta["intake"])]
        )
        # kuhy's own links (his span, the replacement he commented on) stay; Claude may only add stored ones.
        rec = {"draftId": draft_id, "kind": "proposal", "ask": ask, "kuhy": f"{text[start:end]}\n{previous}"}
        rec.update(sources=sources, answer={"text": out["replacement"], "subject": ""})
        return self.shots().answer(rec, out["captures"])

    def gmail(self, draft_id: str) -> Json:
        """Create or update this mail's Gmail draft, only as approved. Never sends."""
        store = self.d.store
        if store.get(draft_id)["meta"]["kind"] != "mail":
            raise ApiError(400, "not-mail")
        try:
            meta, text = approval.approved_mail(store, draft_id)
        except approval.ApprovalError as exc:
            raise ApiError(409, exc.code) from exc
        folder = store.root / "drafts" / draft_id / "assets"

        def load(name: str) -> tuple[Path, bytes, str]:
            data, mime = store.asset(draft_id, name)  # validates the name, sniffs the type
            return folder / name, data, mime

        try:
            mail = mailhtml.build(meta["to"], meta["subject"], text, load)
        except MailError as exc:
            raise ApiError(413 if exc.code == "too-large" else 400, exc.code) from exc
        out = self.d.gmail.upsert(mail, meta["gmailDraftId"], draft_id)
        store.update_meta(draft_id, gmailDraftId=out["id"])
        store.log(draft_id, {"type": "gmail", **out})
        return out

    def export_pdf(self, draft_id: str) -> tuple[bytes, str]:
        """(PDF bytes, Content-Disposition) of the approved text, from the bytes the check hashed."""
        store = self.d.store
        try:
            meta, text = approval.approved_mail(store, draft_id)
        except approval.ApprovalError as exc:
            raise ApiError(409, exc.code) from exc
        title = export.title_of(meta, text)
        page = export.document(title, meta["lang"], text, lambda name: store.asset(draft_id, name))
        try:
            pdf = export.render(page, export.work_dir(store.root))
        except export.ExportError as exc:
            raise ApiError(exc.status, exc.code) from exc
        return pdf, export.disposition(title)

    def approval(self, draft_id: str, action: str, body: Json) -> Json:
        """approve: body {"text": what the editor shows}; unapprove takes no fields."""
        if action == "unapprove":
            return approval.unapprove(self.d.store, draft_id)
        if not isinstance(body.get("text"), str):
            raise ApiError(400, "bad-text")
        try:
            return approval.approve(self.d.store, draft_id, body["text"])
        except approval.ApprovalError as exc:
            raise ApiError(409, exc.code) from exc

    def restore(self, draft_id: str, body: Json) -> Json:
        """Bring back version `v` as a new version."""
        try:
            return self.d.store.restore(draft_id, int(body.get("v", -1)))
        except KeyError as exc:
            raise ApiError(404, "no-version") from exc

    def save(self, draft_id: str, body: Any) -> Json:
        """Autosave (PUT): the store validates every field; `base` is the version it grew from."""
        if not isinstance(body, dict):
            raise ApiError(400, "bad-json")
        base = body.pop("base", None)
        if base is not None and (type(base) is not int or base < 0):
            raise ApiError(400, "bad-save")
        try:
            return self.d.store.save(draft_id, body, base=base)
        except StaleBase as exc:  # another tab saved first: it rebases on this state (sync.ts)
            raise ApiError(409, "stale", extra={"state": exc.state}) from exc
        except ValueError as exc:
            raise ApiError(400, "bad-save") from exc
