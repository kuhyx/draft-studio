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
  clicks the elements you name, outlines `highlight` in red with an arrow and
  stores the picture; the marker becomes that image, or disappears if it fails.
  - The browser keeps the author's login sessions: when a capture needs a login,
    the editor asks him to log in once through its key button, then retries. So
    pages behind a login ARE capturable; request them like any other page.
  - A step-by-step guide on one site is ONE walkthrough: one capture per step,
    in the reader's order, all with the same `walk` id (e.g. "guide") and the same
    start `url`. The browser runs them in one go: each capture's `steps` are only
    the clicks since the previous capture, and `highlight` = the element this step
    tells the reader to click (the red frame). Put each marker line right after its step.
    A single screenshot is a capture with `walk` "" and the clicks from its `url`.
  - The reader's view vs the author's setup: when the author tells you how HE reaches
    the reader's view (e.g. he opens a shares page and clicks „ZALOGUJ JAKO” in the
    owner's row), start the walk at his URL and put his clicks into the `steps` of
    the first capture that needs them. They are never shot and never written into
    the text: the text describes only the reader's own path.
  - `fresh` true: a page the reader sees logged out (a login page). Its `url` is that
    page, it takes no `steps`, and it still joins the walk; every other capture has
    `fresh` false.
  - Targets (each step and `highlight`) are exact, never fuzzy:
    `text` = the element's exact visible text; `within` = the exact text of the group
    heading or table row it sits in, when the text appears more than once
    (e.g. „Domeny” under „Twoje usługi”); an icon-only button (⋮, a gear) has no text:
    `role` "button", `text` "", `within` = its row's text (e.g. the domain name),
    `nth` 1 for the first or -1 for the last button there. Unset fields are "" and 0;
    a highlight with empty `text` and `role` outlines nothing.
  - `url`, and the `text` of a `goto` step (opens another page): only a URL written in
    the brief, the intake answers, the draft, your pending replacement or the author's
    instruction, copied exactly. Never invent or guess a URL or path.
  - Steps click or wait for texts written in the draft or the instruction
    (e.g. Kliknąć „Twoje usługi”) or reported back to you as `seen`. Never click what
    ends, deletes, cancels or buys something (e.g. „ZAKOŃCZ UDOSTĘPNIANIE”): the last
    step of a guide (e.g. „Odnów”) is highlighted, not clicked. The browser never types
    or submits; it asks the author before anything is sent.
  - `key`: a-z, 0-9 and -, unique in the answer.
  - `alt`: what the shot shows at this step, in a few words, no brackets; never a guess.
  - Never write a note about a missing or unavailable screenshot into the text.
    When you cannot request a screenshot for a step, leave the step text exactly as it is.
- The draft is markdown source."""

_AIM_PROPS: dict[str, Any] = {
    "text": {"type": "string", "description": "the element's exact visible text; empty with role"},
    "role": {
        "type": "string",
        "enum": ["", "button"],
        "description": "button: an icon-only button; else empty",
    },
    "within": {
        "type": "string",
        "description": "exact text of its group heading or table row; empty for none",
    },
    "nth": {"type": "integer", "description": "1 first, -1 last match; 0 for none"},
}
AIM = {
    "type": "object",
    "properties": _AIM_PROPS,
    "required": list(_AIM_PROPS),
    "additionalProperties": False,
}
_STEP_PROPS = {
    "kind": {"type": "string", "enum": ["click", "wait", "goto"]},
    **_AIM_PROPS,
    "text": {
        "type": "string",
        "description": "click: exact visible text; wait: text to wait for; goto: a URL",
    },
}
_STEP = {
    "type": "object",
    "properties": _STEP_PROPS,
    "required": list(_STEP_PROPS),
    "additionalProperties": False,
}
_CAPTURE_PROPS = {
    "key": {"type": "string", "description": "a-z0-9-, matches the [[[IMG:<key>]]] line"},
    "walk": {"type": "string", "description": "same id for every capture of one walkthrough; empty for none"},
    "fresh": {
        "type": "boolean",
        "description": "true: the page logged out at `url` (a login page), no steps",
    },
    "url": {"type": "string", "description": "copied exactly from the author's text or instruction"},
    "steps": {"type": "array", "items": _STEP},
    "highlight": AIM,
    "alt": {"type": "string"},
}
CAPTURE = {
    "type": "object",
    "properties": _CAPTURE_PROPS,
    "required": list(_CAPTURE_PROPS),
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
        "`step` is the index of the failing step in this capture's `steps`, or null for its "
        "`highlight`. Return the corrected capture with the same key, walk and url. Change "
        "only the targets of steps and highlight, using only texts written in the draft or "
        "listed in `seen`. For ambiguous-target, each `seen` entry is the text around one "
        'match, its group heading or row first ("heading | row text"): put the part that '
        "tells your target apart into `within`, or pick it with `nth` (1 first, -1 last). "
        "For no-target, `seen` lists the visible labels; an icon-only button has none: "
        'use `role` "button" with an empty `text`, scoped by `within`.\n\n'
        f"Capture:\n{json.dumps(capture, ensure_ascii=False)}\n\n"
        f"Error:\n{json.dumps(error, ensure_ascii=False)}"
    )
