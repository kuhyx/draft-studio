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

from draft_studio.prompts import SYSTEM_PROMPT

FLAT = " ".join(SYSTEM_PROMPT.split())


def test_pages_behind_a_login_are_capturable() -> None:
    assert "keeps the author's login sessions" in FLAT
    assert "pages behind a login ARE capturable" in FLAT
    assert not re.search(r"never[^.]*logs? in", FLAT), "the wording that made Claude skip logged-in steps"


def test_a_step_by_step_guide_gets_one_capture_per_step() -> None:
    assert "one capture per step" in FLAT
    assert "the same start `url`" in FLAT
    assert "`steps` = the clicks of all previous steps" in FLAT
    assert "`highlight` = the element this step tells the reader to click" in FLAT


def test_no_meta_notes_about_missing_screenshots() -> None:
    assert "Never write a note about a missing or unavailable screenshot" in FLAT
    assert "leave the step text exactly as it is" in FLAT
    assert "describe the step in words" not in FLAT


def test_alt_describes_the_step_and_the_old_limits_stay() -> None:
    assert "`alt`: what the shot shows at this step" in FLAT
    assert "Never invent or guess a URL" in FLAT
    assert "never types or submits" in FLAT
    assert "asks the author before anything is sent" in FLAT
