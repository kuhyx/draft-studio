"""The fresh (logged-out) tab: its own browser context, armed before use, the session tab kept."""

from __future__ import annotations

import pytest

from draft_studio.capture import errors
from draft_studio.capture.fresh import fresh_tab
from draft_studio.capture.guard import Guard
from draft_studio.capture.page import CdpPage
from draft_studio.capture.tabs import Tabs
from tests.capture_fakes import FakeCdp


def session() -> tuple[FakeCdp, Tabs]:
    cdp = FakeCdp()
    tabs = Tabs(cdp)
    tabs.attached("main", "s-main")
    tabs.follow("main")
    cdp.answers["Target.createBrowserContext"] = {"browserContextId": "ctx"}
    cdp.answers["Target.createTarget"] = {"targetId": "fresh"}
    cdp.on_pump.append(lambda: tabs.attached("fresh", "s-fresh"))  # the attach handler armed it
    return cdp, tabs


def test_drives_a_new_context_tab_then_closes_it_and_goes_back() -> None:
    cdp, tabs = session()
    with fresh_tab(cdp, tabs):
        assert tabs.target == "fresh" and tabs.session == "s-fresh"
        tabs.close_others()  # what a goto in the fresh tab does: the session tab stays
        assert "main" in tabs.pages
    assert tabs.target == "main" and "fresh" not in tabs.pages
    assert ("Target.createTarget", None, {"url": "about:blank", "browserContextId": "ctx"}) in cdp.sent
    assert ("Target.closeTarget", None, {"targetId": "fresh"}) in cdp.sent
    assert cdp.methods()[-1] == "Target.disposeBrowserContext" and not tabs.keep


def test_a_failure_inside_still_restores_and_disposal_errors_are_ignored() -> None:
    cdp, tabs = session()
    cdp.answers["Target.disposeBrowserContext"] = errors.ChromiumFailed("gone")
    with pytest.raises(errors.NoTarget), fresh_tab(cdp, tabs):
        raise errors.NoTarget(0, "x", [])
    assert tabs.target == "main"


def test_the_session_tab_closing_meanwhile_fails_the_capture() -> None:
    cdp, tabs = session()
    with pytest.raises(errors.ChromiumFailed, match="session tab"), fresh_tab(cdp, tabs):
        tabs.detached("s-main")


def test_cdp_page_fresh_uses_its_tabs() -> None:
    cdp, tabs = session()
    page = CdpPage(cdp, tabs, Guard(cdp, cdp.clock), cdp.clock)
    with page.fresh():
        assert page.session == "s-fresh"
    assert page.session == "s-main"
