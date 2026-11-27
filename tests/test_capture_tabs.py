"""Tabs, and CdpPage following the tab a click opens, over a scripted fake CDP connection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from draft_studio.capture.page import CdpPage, Guard
from draft_studio.capture.policy import Target
from draft_studio.capture.tabs import Tabs
from tests.capture_fakes import FakeCdp, expr_key, paused

BUTTON = Target(ref=1, tag="button", text="Otwórz")


def test_tabs_follow_the_newest_and_forget_closed_ones() -> None:
    cdp = FakeCdp()
    tabs = Tabs(cdp)
    tabs.attached("t0", "blank")
    tabs.attached("t1", "s1")
    before = set(tabs.pages)
    assert tabs.opened_since(before) is None
    tabs.attached("t2", "s2")
    tabs.attached("t3", "s3")
    assert tabs.opened_since(before) == "t3"
    tabs.detached("s3")
    tabs.detached("gone")
    assert tabs.opened_since(before) == "t2"
    tabs.follow("t2")
    assert (tabs.target, tabs.session) == ("t2", "s2")
    metrics = next(p for m, s, p in cdp.sent if m == "Emulation.setDeviceMetricsOverride" and s == "s2")
    assert (metrics["width"], metrics["height"], metrics["deviceScaleFactor"]) == (1280, 800, 2)
    assert ("Page.enable", "s2", {}) in cdp.sent
    tabs.close_others()
    closed = [p["targetId"] for m, _, p in cdp.sent if m == "Target.closeTarget"]
    assert closed == ["t0", "t1"] and tabs.pages == {"t2": "s2"}
    assert cdp.sent[-1] == ("Page.bringToFront", "s2", {})
    cdp.sent.clear()
    tabs.close_others()  # nothing else open: nothing to close, nothing to raise
    assert cdp.sent == []


def tab_page(
    cdp: FakeCdp, values: dict[tuple[str, str], Any], on_press: Callable[[CdpPage], None]
) -> CdpPage:
    """Our tab t1/s1 plus chromium's start tab; `values[(session, expr)]` answers page scripts."""
    tabs = Tabs(cdp)
    tabs.attached("t0", "blank")
    tabs.attached("t1", "s1")
    tabs.target, tabs.session = "t1", "s1"
    page = CdpPage(cdp, tabs, Guard(cdp, cdp.clock), cdp.clock)
    defaults = {"__ds.point(1)": {"x": 5, "y": 5, "hit": True}, "__ds.takeSubmits()": []}

    def evaluate(params: dict[str, Any]) -> Any:
        session, expr = cdp.sent[-1][1], expr_key(params)
        value = values.get((session, expr), defaults.get(expr, "complete"))
        return {"result": {"value": value(page) if callable(value) else value}}

    def mouse(params: dict[str, Any]) -> Any:
        if params["type"] == "mousePressed":
            on_press(page)
        return {}

    cdp.answers["Runtime.evaluate"] = evaluate
    cdp.answers["Input.dispatchMouseEvent"] = mouse
    return page


def evaluated(cdp: FakeCdp, expr: str) -> list[str | None]:
    return [s for m, s, p in cdp.sent if m == "Runtime.evaluate" and expr_key(p) == expr]


def test_a_clean_click_follows_the_newest_tab_it_opened() -> None:
    cdp = FakeCdp()

    def opens_two(page: CdpPage) -> None:
        page.tabs.attached("t2", "s2")
        page.tabs.attached("t3", "s3")

    loading = iter(["loading", "loading"])
    page = tab_page(cdp, {("s3", "document.readyState"): lambda _: next(loading, "complete")}, opens_two)
    assert page.click(BUTTON, allow_submit=True, allowed=[]) == []
    assert page.session == "s3" and page.tabs.pages == {"t3": "s3"}
    assert evaluated(cdp, "document.readyState").count("s3") == 3  # waited for the new tab to load
    assert evaluated(cdp, "__ds.takeSubmits()") == ["s1", "s3"]
    assert evaluated(cdp, "__ds.allow = false") == ["s1"]
    closed = [p["targetId"] for m, _, p in cdp.sent if m == "Target.closeTarget"]
    assert closed == ["t0", "t1", "t2"] and cdp.sent[-1] == ("Page.bringToFront", "s3", {})
    assert ("Emulation.setDeviceMetricsOverride", "s3") in [(m, s) for m, s, _ in cdp.sent]
    page.url()
    assert cdp.sent[-1][1] == "s3"  # later steps run on the followed tab


def test_a_refused_click_stays_on_the_clicked_tab() -> None:
    cdp = FakeCdp()

    def opens_and_posts(page: CdpPage) -> None:
        page.tabs.attached("t2", "s2")
        page.guard.handle(paused("POST", "http://x/api/tab", "r7", session="s2"))

    page = tab_page(cdp, {("s2", "__ds.takeSubmits()"): ["http://x/form"]}, opens_and_posts)
    blocked = page.click(BUTTON, allow_submit=False, allowed=[])
    assert blocked == [("POST", "http://x/api/tab"), ("SUBMIT", "http://x/form")]
    assert page.session == "s1" and page.tabs.pages == {"t1": "s1"}  # the refusal's shot outlines BUTTON
    assert ("Target.closeTarget", None, {"targetId": "t2"}) in cdp.sent
    assert cdp.sent[-1] == ("Page.bringToFront", "s1", {})


def test_a_new_tab_that_closes_itself_is_not_followed() -> None:
    cdp = FakeCdp()

    def closes(page: CdpPage) -> str:
        page.tabs.detached("s2")
        return "loading"

    page = tab_page(cdp, {("s2", "document.readyState"): closes}, lambda p: p.tabs.attached("t2", "s2"))
    assert page.click(BUTTON, allow_submit=False, allowed=[]) == []
    assert evaluated(cdp, "document.readyState").count("s2") == 1  # stopped waiting once it went away
    assert page.session == "s1" and evaluated(cdp, "__ds.takeSubmits()") == ["s1"]


def test_an_opener_that_closes_itself_hands_over_to_its_new_tab() -> None:
    cdp = FakeCdp()

    def replaces(page: CdpPage) -> None:
        page.tabs.attached("t2", "s2")
        page.tabs.detached("s1")

    page = tab_page(cdp, {}, replaces)
    assert page.click(BUTTON, allow_submit=True, allowed=[]) == []
    assert evaluated(cdp, "document.readyState").count("s1") == 0
    assert evaluated(cdp, "__ds.takeSubmits()") == ["s2"] and evaluated(cdp, "__ds.allow = false") == []
    assert page.session == "s2" and page.tabs.pages == {"t2": "s2"}
