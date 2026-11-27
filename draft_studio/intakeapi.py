"""The brief and intake rounds: ask Claude the next questions, or write the first draft.

Both are jobs (jobs.py). What they land is tagged with the job id in the same write
(`Store.save(job=...)`, the held record), so a replay after a restart lands once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from draft_studio.apiutil import ApiError, Json
from draft_studio.claude import MAX_INTAKE_ROUNDS

if TYPE_CHECKING:
    from draft_studio.api import Api


def next_step(api: Api, draft_id: str) -> Json:
    """Ask the next intake round, or write the first draft when Claude is ready."""
    store, claude = api.d.store, api.d.claude
    state = store.get(draft_id)
    tag: dict[str, Any] = api.d.jobs.tag() if api.d.jobs else {}
    rounds = len({item["round"] for item in state["meta"]["intake"]})
    result: Json = {"lang": state["meta"]["lang"], "questions": []}
    if rounds < MAX_INTAKE_ROUNDS:
        result = claude.intake(api._ctx(draft_id, state))
    if not state["meta"]["lang"]:
        state = store.save(draft_id, {"lang": result["lang"]}, kind="lang")
    if result["questions"]:
        pending = [{"q": q, "a": None, "round": rounds} for q in result["questions"]]
        patch = {"intake": state["meta"]["intake"] + pending, "phase": "intake"}
        state = store.save(draft_id, patch, kind="intake", job=tag.get("job"))
        return {"questions": result["questions"], "state": state, "stripped": []}
    draft = claude.draft(api._ctx(draft_id, state))
    meta = state["meta"]
    kuhy = "\n".join([meta["brief"], state["text"], *(item["a"] or "" for item in meta["intake"])])
    rec = {"draftId": draft_id, "kind": "draft", "ask": {}, "kuhy": kuhy, "sources": kuhy, **tag}
    rec["answer"] = {"text": draft["text"], "subject": draft["subject"]}
    return api.shots().answer(rec, draft["captures"])


def brief(api: Api, draft_id: str, body: Json) -> Json:
    """Store the brief and start intake."""
    text = body.get("brief")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, "bad-brief")
    api.d.store.save(draft_id, {"brief": text, "intake": []}, kind="brief")
    return next_step(api, draft_id)


def intake(api: Api, draft_id: str, body: Json) -> Json:
    """Fill the open questions with answers, then take the next step."""
    answers = body.get("answers")
    if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
        raise ApiError(400, "bad-answers")
    state = api.d.store.get(draft_id)
    items = [dict(item) for item in state["meta"]["intake"]]
    open_items = [item for item in items if item["a"] is None]
    for item, answer in zip(open_items, answers, strict=False):
        item["a"] = answer
    for item in open_items[len(answers) :]:
        item["a"] = ""
    api.d.store.save(draft_id, {"intake": items}, kind="intake")
    return next_step(api, draft_id)
