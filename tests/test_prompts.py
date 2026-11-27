"""What the system prompt teaches Claude about screenshots.

Incident (2026-11-27): asked for "screenshots with red arrow pointing to what to
click for each step" of a 7-step panel guide, Claude shot only the start page
and wrote "(bez zrzutu ekranu - widoczne dopiero po zalogowaniu)" into the
other steps. The old prompt said the browser "never ... logs in", which Claude
read as "pages behind a login cannot be shot". The live check for the same
shape is scripts/live_shots_check.py.
"""

from __future__ import annotations

import re
from typing import Any

from draft_studio.prompts import (
    AIM,
    CAPTURE,
    DRAFT_SCHEMA,
    FIX_SCHEMA,
    REWRITE_SCHEMA,
    SYSTEM_PROMPT,
    fix_task,
)

FLAT = " ".join(SYSTEM_PROMPT.split())


def test_pages_behind_a_login_are_capturable() -> None:
    assert "keeps the author's login sessions" in FLAT
    assert "pages behind a login ARE capturable" in FLAT
    assert not re.search(r"never[^.]*logs? in", FLAT), "the wording that made Claude skip logged-in steps"


def test_a_step_by_step_guide_is_one_walkthrough() -> None:
    assert "is ONE walkthrough: one capture per step" in FLAT
    assert "the same `walk` id" in FLAT and "the same start `url`" in FLAT
    assert "each capture's `steps` are only the clicks since the previous capture" in FLAT
    assert "`highlight` = the element this step tells the reader to click" in FLAT


def test_the_authors_setup_is_run_but_never_written() -> None:
    """kuhy reaches the owner's panel through „ZALOGUJ JAKO”; the guide is for the owner."""
    assert "start the walk at his URL" in FLAT
    assert "never shot and never written into the text" in FLAT
    assert "the author's instruction" in FLAT  # his instruction's URLs are allowed
    assert "`fresh` true: a page the reader sees logged out" in FLAT
    assert "Never click what ends, deletes, cancels or buys" in FLAT and "„ZAKOŃCZ UDOSTĘPNIANIE”" in FLAT


def test_targets_are_exact_and_icon_buttons_have_a_role() -> None:
    assert "`within` = the exact text of the group heading or table row" in FLAT
    assert '`role` "button", `text` ""' in FLAT and "`nth` 1 for the first or -1 for the last" in FLAT


def test_every_schema_property_is_required_so_sentinels_fill_them() -> None:
    def objects(node: object) -> list[dict[str, Any]]:
        if isinstance(node, dict):
            own = [node] if node.get("type") == "object" else []
            return own + [o for v in node.values() for o in objects(v)]
        return [o for v in node for o in objects(v)] if isinstance(node, list) else []

    found = objects([DRAFT_SCHEMA, REWRITE_SCHEMA, FIX_SCHEMA])
    assert found and all(set(o["required"]) == set(o["properties"]) for o in found)
    assert CAPTURE["properties"]["highlight"] == AIM


def test_a_fix_explains_scopes_and_the_seen_surroundings() -> None:
    task = " ".join(fix_task({"key": "a"}, {"error": "ambiguous-target"}).split())
    assert "same key, walk and url" in task and '"heading | row text"' in task
    assert "into `within`, or pick it with `nth`" in task and '`role` "button" with an empty `text`' in task


def test_no_meta_notes_about_missing_screenshots() -> None:
    assert "Never write a note about a missing or unavailable screenshot" in FLAT
    assert "leave the step text exactly as it is" in FLAT
    assert "describe the step in words" not in FLAT


def test_alt_describes_the_step_and_the_old_limits_stay() -> None:
    assert "`alt`: what the shot shows at this step" in FLAT
    assert "Never invent or guess a URL" in FLAT
    assert "never types or submits" in FLAT
    assert "asks the author before anything is sent" in FLAT
