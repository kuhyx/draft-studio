"""A guide's login-page capture is a logged-out shot, however Claude flagged it (kuhy, 2026-11-27).

Incident: step 1 "screenshot of https://panel.cyberfolks.pl/security/login" came as a session
capture (`fresh` false), so the run opened the login page as the session and asked for a login.
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.shotaim import ShotError
from draft_studio.shotreq import parse_shot
from draft_studio.walks import walk_spec

LOGIN = "https://panel.cyberfolks.pl/security/login"
SHARES = "https://panel.cyberfolks.pl/your-data/shares"
AIM = {"text": "", "role": "", "within": "", "nth": 0}


def cap(key: str, url: str, steps: list[Any] | None = None, fresh: bool = False) -> Any:
    raw = {
        "key": key,
        "walk": "g",
        "fresh": fresh,
        "url": url,
        "steps": steps or [],
        "highlight": AIM,
        "alt": key,
    }
    return parse_shot(raw)


def test_a_stepless_session_capture_of_a_login_url_runs_logged_out() -> None:
    spec, at = walk_spec(
        [cap("login", LOGIN), cap("next", SHARES, [{"kind": "click", **AIM, "text": "Domeny"}])]
    )
    assert spec["url"] == SHARES  # the login capture is not a second start
    assert spec["steps"][0] == {"shot": {"name": "login", "highlight": None, "fresh": True, "url": LOGIN}}
    assert at == [0, 2]


def test_a_login_capture_that_clicks_stays_a_session_capture() -> None:
    with pytest.raises(ShotError):  # two starts: the walk is malformed, as before
        walk_spec([cap("login", LOGIN, [{"kind": "click", **AIM, "text": "x"}]), cap("n", SHARES)])


def test_a_walk_of_login_url_captures_only_starts_there() -> None:
    spec, _ = walk_spec([cap("login", LOGIN), cap("again", LOGIN)])
    assert spec["url"] == LOGIN  # nothing else to start from: the runner shoots both logged out
