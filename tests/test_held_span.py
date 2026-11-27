"""A held answer's span follows the draft text (kuhy: another tab drew it at the ask-time positions).

The listing maps the ask-time span through every edit since the ask, the way `proposalapi.land`
does when the answer is released, so every tab (and a reloaded one) draws the same, current range.
"""

from __future__ import annotations

from typing import Any

from draft_studio.proposals import width
from tests.conftest import App
from tests.test_held import SAVE, grant, held, saving
from tests.test_shots import propose, text_draft

HEAD, TAIL = "Head line.\n\n", "\n\nTail line."


def hold_in_context(app: App) -> tuple[str, dict[str, Any]]:
    """A hold on SAVE with text before and after it."""
    did = text_draft(app, f"{HEAD}{SAVE}{TAIL}")
    status, body = propose(app, did, SAVE)
    assert status == 202, body
    return did, body["held"]


def span_of(app: App, did: str) -> tuple[Any, Any]:
    (h,) = held(app, did)["held"]
    return h["from"], h["to"]


def edit(app: App, did: str, text: str) -> None:
    app.store.save(did, {"text": text})


def test_the_view_starts_at_the_ask_positions(app: App) -> None:
    saving(app)
    did, h = hold_in_context(app)
    assert (h["from"], h["to"]) == (len(HEAD), len(HEAD) + len(SAVE))
    assert span_of(app, did) == (h["from"], h["to"])


def test_an_insert_before_the_span_moves_it(app: App) -> None:
    saving(app)
    did, _ = hold_in_context(app)
    edit(app, did, f"Intro 😀 line.\n\n{HEAD}{SAVE}{TAIL}")
    start = width(f"Intro 😀 line.\n\n{HEAD}")
    assert span_of(app, did) == (start, start + width(SAVE))


def test_an_insert_after_the_span_leaves_it(app: App) -> None:
    saving(app)
    did, _ = hold_in_context(app)
    edit(app, did, f"{HEAD}{SAVE}{TAIL} More.")
    assert span_of(app, did) == (len(HEAD), len(HEAD) + len(SAVE))


def test_several_edits_before_the_span_add_up(app: App) -> None:
    saving(app)
    did, _ = hold_in_context(app)
    edit(app, did, f"A.\n\n{HEAD}{SAVE}{TAIL}")
    edit(app, did, f"A.\n\nBBB.\n\n{HEAD}{SAVE}{TAIL}")
    edit(app, did, f"A.\n\nBB.\n\n{HEAD}{SAVE}{TAIL}")
    start = len(f"A.\n\nBB.\n\n{HEAD}")
    assert span_of(app, did) == (start, start + len(SAVE))


def test_an_edit_inside_the_span_has_no_span_left_to_draw(app: App) -> None:
    saving(app)
    did, _ = hold_in_context(app)
    edit(app, did, f"{HEAD}{SAVE.replace('kliknąć', 'nacisnąć')}{TAIL}")
    assert span_of(app, did) == (None, None)


def test_the_span_agrees_with_where_the_answer_lands(app: App) -> None:
    saving(app)
    did, h = hold_in_context(app)
    edit(app, did, f"Intro.\n\n{HEAD}{SAVE}{TAIL}")
    (listed,) = held(app, did)["held"]
    assert grant(app, h["requests"][0]["id"])[0] == 200
    (proposal,) = app.store.get(did)["proposals"]
    assert (listed["from"], listed["to"]) == (proposal["from"], proposal["to"])
