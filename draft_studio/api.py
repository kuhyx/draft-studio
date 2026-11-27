"""JSON route bodies, independent of the HTTP plumbing in server.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from draft_studio import approval, export, mailhtml
from draft_studio.claude import MAX_INTAKE_ROUNDS, Claude, Context
from draft_studio.mail import Gmail, MailError
from draft_studio.store import Store

Json = dict[str, Any]
CLOSE = {"Connection": "close"}


class ApiError(Exception):
    """An error with an HTTP status and a short machine code (shown as an icon)."""

    def __init__(self, status: int, code: str, close: bool = False) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        # close: the request body was left unread, so the keep-alive connection must end.
        self.headers = CLOSE if close else {}


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

    def save(self, draft_id: str, body: Any) -> Json:
        """Autosave (PUT): the store validates every field."""
        if not isinstance(body, dict):
            raise ApiError(400, "bad-json")
        try:
            return self.d.store.save(draft_id, body)
        except ValueError as exc:
            raise ApiError(400, "bad-save") from exc
