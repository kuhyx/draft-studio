"""What `claude -p` is told: the system prompt and the JSON schema of every answer.

Screenshots (kuhy, 2026-11-27: "the draft logic itself should take those
screenshots"): a draft or rewrite answer may carry `captures`, each one a
real screenshot the server takes before kuhy sees the answer. Claude writes
the marker line `[[[IMG:<key>]]]` where the image goes; draft_studio/shots.py
swaps it for `![alt](assets/<stored png>)` or drops the line.
"""

from __future__ import annotations

import json
from typing import Any

MARKER = "[[[IMG:{key}]]]"

SYSTEM_PROMPT = """You co-write text drafts with the author in a draft editor.
Rules:
- Reply only through the JSON schema you are given.
- Write in the draft's language (BCP 47 tag in the context). If the tag is empty, use the brief's language.
- When a span is marked with [[[SEL]]] ... [[[/SEL]]], work on that span only.
  A rewrite returns only the replacement for that span, without the markers,
  fitting seamlessly into the surrounding text and keeping the draft's tone.
- Never invent facts the author did not give; ask instead during intake.
- Never invent image links, image file names or image placeholders. You may
  link only the files listed under "Stored images", exactly as ![alt](assets/<name>);
  when none are listed, write no image link at all.
- Screenshots: when a step of a how-to would be clearer with a screenshot of a
  web page, request one in `captures` and put the line [[[IMG:<key>]]] on its own
  line where the image goes. The editor opens the page in a real browser,
  clicks the elements you name, outlines `highlight` in red and stores the
  picture; the marker becomes that image, or disappears if it fails.
  - `url`: only a URL written in the brief, the intake answers, the draft or your
    pending replacement, copied exactly. Never invent or guess a URL or path.
  - `steps`: click or wait for elements by their visible text, using only texts
    written in the draft (e.g. Kliknąć „Twoje usługi”) or reported back to you as
    `seen`. The browser only reads: it never types, logs in or submits.
  - `key`: a-z, 0-9 and -, unique in the answer. `alt`: a few words, no brackets.
  - No suitable URL: request nothing and describe the step in words.
- The draft is markdown source."""

_STEP = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["click", "wait"]},
        "text": {"type": "string", "description": "visible text of the element"},
    },
    "required": ["kind", "text"],
    "additionalProperties": False,
}
CAPTURE = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "a-z0-9-, matches the [[[IMG:<key>]]] line"},
        "url": {"type": "string", "description": "copied exactly from the brief, answers or draft"},
        "steps": {"type": "array", "items": _STEP},
        "highlight": {"type": "string", "description": "visible text to outline in red; empty for none"},
        "alt": {"type": "string"},
    },
    "required": ["key", "url", "steps", "highlight", "alt"],
    "additionalProperties": False,
}
_CAPTURES = {"type": "array", "items": CAPTURE}


def _schema(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


INTAKE_SCHEMA = _schema(
    lang={"type": "string", "description": "BCP 47 tag of the brief's language, e.g. en, pl"},
    questions={"type": "array", "items": {"type": "string"}},
)
DRAFT_SCHEMA = _schema(
    text={"type": "string"},
    subject={"type": "string", "description": "mail subject; empty for a text draft"},
    captures=_CAPTURES,
)
ASK_SCHEMA = _schema(answer={"type": "string"})
REWRITE_SCHEMA = _schema(replacement={"type": "string"}, captures=_CAPTURES)
FIX_SCHEMA = _schema(capture=CAPTURE)


def fix_task(capture: dict[str, Any], error: dict[str, Any]) -> str:
    """The task of a correction call: one capture failed to find its element."""
    return (
        "A screenshot you requested failed: the browser did not find a unique element. "
        "Return the corrected capture with the same key and url. Change only step and "
        "highlight texts, and only to texts written in the draft or listed in `seen`.\n\n"
        f"Capture:\n{json.dumps(capture, ensure_ascii=False)}\n\n"
        f"Error:\n{json.dumps(error, ensure_ascii=False)}"
    )
