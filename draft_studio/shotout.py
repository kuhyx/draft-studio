"""What leaves the screenshot pipeline: the held view the client polls and the released answer.

Split from shots.py (which runs the captures). `release` swaps every resolved
marker for its stored image, cuts the rest, puts back the old images the answer
lost (keepimg.py), runs links.py `strip_invented` as the last gate, and logs the
answer in the draft's history.

kuhy (2026-11-27: "it suggested edits but the images stayed the same"): a proposal whose every
planned shot was dropped is never released as a text-only replacement. `failed` holds it; `lost`
names the shots and why, for the card (held_view) and for the retry (heldapi.py).
"""

from __future__ import annotations

import logging
from typing import Any

from draft_studio import keepimg, proposalapi
from draft_studio.capture.pending import PendingStore
from draft_studio.claude import ClaudeError
from draft_studio.held import WAITING, HeldStore
from draft_studio.links import LinkError, strip_invented
from draft_studio.proposals import cut as span_of
from draft_studio.shotreq import markers, substitute
from draft_studio.store import Store

Json = dict[str, Any]
LOG = logging.getLogger("draft_studio")


def stored(store: Store, draft_id: str) -> tuple[str, ...]:
    """Names of the draft's stored images: the only ones Claude may link."""
    return tuple(item["name"] for item in store.list_assets(draft_id))


def strip(store: Store, rec: Json, text: str) -> tuple[str, list[str]]:
    """Claude's text without the image links it invented; ClaudeError when one cannot be cut."""
    try:
        out, cut = strip_invented(text, stored(store, rec["draftId"]), rec["kuhy"])
    except LinkError as exc:
        raise ClaudeError(f"dropped: {exc}") from exc
    if cut:
        LOG.warning("stripped invented image links: %s", cut)
    return out, cut


def lost(rec: Json) -> list[Json]:
    """The shots the text asks for (marker lines) that did not resolve: {"key", "reason"} each."""
    done = {k for k, s in rec["shots"].items() if s["state"] == "done"}
    reasons = {d["key"]: d["reason"] for d in rec["dropped"]}
    reasons.update({k: s["reason"] for k, s in rec["shots"].items() if s["state"] == "dropped"})
    return [
        {"key": k, "reason": reasons.get(k) or "capture"}
        for k in markers(rec["answer"]["text"])
        if k not in done
    ]


def failed(rec: Json) -> bool:
    """A proposal that asks for screenshots and none of them was taken, with nothing left to wait for."""
    if rec["kind"] != "proposal" or any(s["state"] in WAITING for s in rec["shots"].values()):
        return False
    wanted = markers(rec["answer"]["text"])
    return bool(wanted) and len(lost(rec)) == len(wanted)


def held_view(pending: PendingStore, rec: Json) -> Json:
    """What the client sees of a held answer (documented in capture/__init__.py)."""
    slots = rec["shots"].values()
    rids = {s["rid"] for s in slots if s["state"] == "pending"}
    ask = rec["ask"]
    return {
        "id": rec["id"],
        "kind": rec["kind"],
        "from": ask.get("from"),
        "to": ask.get("to"),
        "requests": [r for r in pending.list_pending() if r["id"] in rids],
        "login": sorted({s["origin"] for s in slots if s["state"] == "login"}),
        "busy": any(s["state"] == "busy" for s in slots),
        "tries": rec.get("tries", 0),
        "failed": lost(rec) if failed(rec) else [],
    }


def _kept_images(rec: Json, done: Json) -> tuple[str, Json]:
    """(answer text, markers to swap) with the old images a proposal must keep (keepimg.py)."""
    text = rec["answer"]["text"]
    if rec["kind"] != "proposal":
        return text, {}
    ask = rec["ask"]
    baselines = [span_of(ask["text"], ask["from"], ask["to"]), ask["previous"]]
    return keepimg.restore(text, baselines, ask["instruction"], done)


def release(store: Store, held: HeldStore, rec: Json) -> Json:
    """The final text: images in, failed lines out, last gate run; logged in the draft's history."""
    done = {k: s["md"] for k, s in rec["shots"].items() if s["state"] == "done"}
    text, cut = substitute(rec["answer"]["text"], done)
    reasons = {k: s["reason"] for k, s in rec["shots"].items()}
    listed = {d["key"] for d in rec["dropped"]}
    rec["dropped"] += [{"key": k, "reason": reasons.get(k) or "capture"} for k in cut if k not in listed]
    kept, back = _kept_images(rec, done)
    if back:  # the old image stays where its new shot is missing (kuhy: never a silent loss)
        text, _ = substitute(kept, {**done, **back})
    try:
        text, _ = strip_invented(text, stored(store, rec["draftId"]), rec["kuhy"])
    except LinkError:  # never expected: the swapped-in links are clean; fall back to no images
        text, _ = substitute(rec["answer"]["text"], {})
    if rec["dropped"]:
        LOG.warning("dropped screenshots: %s", rec["dropped"])
    result = _deliver(store, rec, text)
    if "id" in rec:
        rec.update(status="released", result=result)
        held.put(rec)
    return result


def _deliver(store: Store, rec: Json, text: str) -> Json:
    did, ask, dropped = rec["draftId"], rec["ask"], rec["dropped"]
    # partial: a screenshot the answer asked for is missing; the proposal keeps which (`missing`).
    extra = {"stripped": rec["stripped"], "dropped": dropped, "partial": bool(dropped)}
    if rec["kind"] == "proposal":
        out = {"replacement": text, "from": ask["from"], "to": ask["to"], **extra}
        missing = [d["key"] for d in dropped]
        # The proposal now lives on the server.
        out.update(proposalapi.land(store, did, ask, text, job=rec.get("job", ""), missing=missing))
        entry = {
            "type": "proposal",
            "instruction": ask["instruction"],
            "previous": ask["previous"],
            **out,
        }
        store.log(did, entry)
        return out
    meta = store.get(did)["meta"]
    patch: Json = {"text": text, "phase": "editing", "claude": [[0, len(text)]] if text else []}
    subject = rec["answer"]["subject"]
    if meta["kind"] == "mail" and meta["subjectAuthor"] != "you" and subject:
        patch.update(subject=subject, subjectAuthor="claude")
    state = store.save(did, patch, kind="draft", job=rec.get("job"))  # the landing: one atomic write
    if rec["stripped"]:
        store.log(did, {"type": "stripped", "stripped": rec["stripped"]})
    if rec["dropped"]:
        store.log(did, {"type": "dropped", "dropped": rec["dropped"]})
    return {"questions": [], "state": state, **extra}
