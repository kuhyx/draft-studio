"""Tooltips on icon-only buttons: hover, keyboard focus, touch long-press, demos.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_tooltips.py
Screenshots (desktop hover, 390 px long-press) go to $DS_SHOTS.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.cdp import Browser, free_port

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
EN = json.loads((REPO / "locales" / "en.json").read_text())
PL = json.loads((REPO / "locales" / "pl.json").read_text())
TEXT = "Dear owner,\n\nalpha beta gamma delta and some more words here.\n"

BUBBLE = """(() => {
  const t = document.querySelector('ds-tip');
  if (!t || !t.matches(':popover-open')) return null;
  const r = t.getBoundingClientRect();
  const demo = t.querySelector('.tip-demo');
  return {text: t.querySelector('.tip-text').textContent, demo: !!demo && !demo.hidden,
          moving: demo && !demo.hidden ? demo.getAnimations({subtree: true}).length : 0,
          l: r.left, t: r.top, r: r.right, b: r.bottom, vw: innerWidth, vh: innerHeight,
          hidden: t.getAttribute('aria-hidden')};
})()"""


def rect(b: Browser, sel: str) -> dict[str, float]:
    b.center(sel)  # waits until the element stops moving
    out: dict[str, float] = b.js(
        f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect();"
        " return {l: r.left, t: r.top, r: r.right, b: r.bottom} })()"
    )
    return out


def bubble(b: Browser, text: str | None = None, timeout: float = 3) -> dict[str, Any]:
    cond = "true" if text is None else f"v.text === {json.dumps(text)}"
    out: dict[str, Any] = b.wait(
        f"(() => {{ const v = {BUBBLE}; return v && {cond} ? v : null }})()", timeout, what=f"bubble {text}"
    )
    return out


def no_bubble(b: Browser) -> None:
    b.wait(f"{BUBBLE} === null", 3, what="bubble hidden")


def inside(v: dict[str, Any]) -> bool:
    return bool(v["l"] >= 0 and v["t"] >= 0 and v["r"] <= v["vw"] and v["b"] <= v["vh"])


def post(base: str, path: str, body: object, method: str = "POST") -> dict[str, Any]:
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        out: dict[str, Any] = json.loads(r.read())
        return out


@pytest.fixture(scope="module")
def site() -> Iterator[tuple[str, str, Path]]:
    tmp = Path(tempfile.mkdtemp(prefix="ds-tip-"))
    shots = Path(os.environ.get("DS_SHOTS", tmp / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    environ = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(tmp / "data"),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_GMAIL": "fake",
    }
    server = subprocess.Popen([sys.executable, "-m", "draft_studio", "--port", str(port)], cwd=REPO, env=environ)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/version", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    did = str(post(base, "/api/drafts", {"kind": "mail"})["id"])
    post(base, f"/api/drafts/{did}/brief", {"brief": "Mail to the owner [noq]"})
    post(base, f"/api/drafts/{did}", {"text": TEXT, "claude": [], "to": "", "subject": "Hi"}, method="PUT")
    yield base, did, shots
    server.terminate()
    server.wait(timeout=10)
    shutil.rmtree(tmp, ignore_errors=True)


def select(b: Browser, needle: str) -> None:
    i = TEXT.index(needle)
    b.js(f"draftStudio.view.focus(); draftStudio.view.dispatch({{selection: {{anchor: {i}, head: {i + len(needle)}}}}})")
    b.wait("document.querySelector('.sel-toolbar .sel-ask') !== null", what="toolbar")


def doc(b: Browser) -> str:
    return str(b.js("draftStudio.view.state.doc.toString()"))


def propose(b: Browser, tap: bool = False) -> None:
    select(b, "alpha beta gamma")
    if tap:
        b.touch(".sel-toolbar .sel-rewrite", 0.05)
    else:
        b.click(".sel-toolbar .sel-rewrite")
    b.wait("document.querySelector('.cm-hunk') !== null", 15, what="diff shown")


def test_desktop_hover_focus_demo_locale(site: tuple[str, str, Path]) -> None:
    base, did, shots = site
    b = Browser(1920, 1080)
    try:
        # C1/C2 list page: every icon-only button shows its tip; away and Escape hide it.
        b.goto(base + "/")
        b.wait("document.querySelector('.draft-row .delete') !== null")
        for sel, key in (("#new-mail", "newMail"), ("#new-text", "newText"), ("#ui-locale", "uiLocale"),
                         (".draft-row .delete", "delete")):
            b.hover(sel)
            v = bubble(b, EN["tips"][key])
            assert inside(v) and not v["demo"] and v["hidden"] == "true", (sel, v)
        b.mouse_to(900, 600)
        no_bubble(b)
        b.hover("#new-mail")
        bubble(b, EN["tips"]["newMail"])
        b.key("Escape", "Escape", 27)
        no_bubble(b)
        # A closed bubble adds no visible text.
        assert EN["tips"]["newMail"] not in b.js("document.body.innerText")
        # Every icon-only button on the page has a tip.
        assert b.js("[...document.querySelectorAll('.icon-btn')].every(e => e.dataset.label)")

        # C3: keyboard focus shows the bubble; Tab moves it on.
        b.mouse_to(900, 600)
        b.js("document.activeElement?.blur(); document.body.focus()")
        b.key("Tab", "Tab", 9)
        b.wait("document.activeElement?.id === 'new-mail'", what="focus new-mail")
        bubble(b, EN["tips"]["newMail"])
        b.key("Tab", "Tab", 9)
        bubble(b, EN["tips"]["newText"])
        b.js("document.activeElement.blur()")
        no_bubble(b)

        # Editor: text-only tips (C6), every icon button labelled.
        b.goto(f"{base}/d/?id={did}")
        for sel, key in (("#undo", "undo"), ("#history-open", "history"), ("#ui-locale", "uiLocale"),
                         ("header .icon-btn[data-label='back']", "back"), ("#gmail", "gmail")):
            b.hover(sel)
            v = bubble(b, EN["tips"][key])
            assert not v["demo"] and inside(v), (sel, v)

        # C4: selection toolbar demos.
        for cls, key in (("sel-ask", "ask"), ("sel-rewrite", "rewrite"), ("sel-change", "change")):
            select(b, "alpha beta")
            b.hover(f".sel-toolbar .{cls}")
            v = bubble(b, EN["tips"][key])
            assert v["demo"] and v["moving"] > 0, (key, v)
            if key == "ask":
                time.sleep(2.2)
                b.screenshot(shots / "tip-hover-ask-1920.png")
        # The demo is the built-in sample, never the draft text.
        assert "alpha" not in b.js("document.querySelector('ds-tip').textContent")

        # C5: accept/reject demos, clicking still acts at once.
        b.mouse_to(900, 900)
        propose(b)
        b.hover(".cm-hunk .hunk-accept")
        v = bubble(b, EN["tips"]["acceptHunk"])
        assert v["demo"] and v["moving"] > 0
        b.hover(".cm-hunk .hunk-reject")
        v = bubble(b, EN["tips"]["rejectHunk"])
        assert v["demo"] and v["moving"] > 0
        n = b.js("document.querySelectorAll('.cm-hunk').length")
        b.click(".cm-hunk .hunk-accept")
        b.wait(f"document.querySelectorAll('.cm-hunk').length === {n - 1}", what="accepted")
        b.hover(".cm-hunk-all .reject-all")
        bubble(b, EN["tips"]["rejectAll"])
        b.click(".cm-hunk-all .reject-all")
        b.wait("document.querySelector('.cm-hunk') === null")

        # C1 history panel (top layer): restore and close show above the dialog.
        b.click("#history-open")
        b.wait("document.querySelector('#history').open")
        b.hover("#history .restore")
        assert inside(bubble(b, EN["tips"]["restore"]))
        b.hover("#history-close")
        bubble(b, EN["tips"]["close"])
        b.click("#history-close")

        # C10: reduced motion -> text plus one still end frame.
        b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        b.mouse_to(900, 900)
        select(b, "alpha beta")
        b.hover(".sel-toolbar .sel-ask")
        v = bubble(b, EN["tips"]["ask"])
        assert v["demo"] and v["moving"] == 0, v
        b.call("Emulation.setEmulatedMedia", features=[])

        # C11: Polish tips; aria-label stays the full screen-reader label.
        b.mouse_to(900, 900)
        b.click("#ui-locale")
        b.wait("document.documentElement.lang === 'pl'")
        b.hover("#undo")
        bubble(b, PL["tips"]["undo"])
        assert b.js("document.querySelector('#undo').getAttribute('aria-label')") == PL["labels"]["undo"]
        b.click("#ui-locale")
        b.wait("document.documentElement.lang === 'en'")
        assert b.errors() == []
    finally:
        b.close()


def test_touch_long_press_and_tap(site: tuple[str, str, Path]) -> None:
    base, did, shots = site
    b = Browser(390, 844, mobile=True)
    try:
        b.goto(f"{base}/d/?id={did}")
        before = doc(b)
        # C7/C8: long-press rewrite -> bubble above, stays while held; lift hides it, no action.
        select(b, "alpha beta")
        btn = rect(b, ".sel-toolbar .sel-rewrite")

        def held() -> dict[str, Any]:
            bubble(b, EN["tips"]["rewrite"])
            time.sleep(1.8)  # let the demo reach its end frame
            b.screenshot(shots / "tip-longpress-rewrite-390.png")
            return bubble(b, EN["tips"]["rewrite"])

        v = b.touch(".sel-toolbar .sel-rewrite", 0.8, held)
        assert v["b"] <= btn["t"] and inside(v) and v["demo"], (v, btn)
        no_bubble(b)
        time.sleep(0.5)
        assert not b.js("document.body.dataset.busy") and doc(b) == before
        assert b.js("document.querySelector('.cm-hunk')") is None

        # C9: a quick tap acts at once and leaves no bubble.
        b.touch(".sel-toolbar .sel-ask", 0.05)
        b.wait("!document.querySelector('.sel-form').hidden", 3, what="ask form open")
        no_bubble(b)

        # Header long-press at the top edge: the bubble never sits under the finger.
        hb = rect(b, "#undo")
        v = b.touch("#undo", 0.8, lambda: bubble(b, EN["tips"]["undo"]))
        assert inside(v) and (v["b"] <= hb["t"] or v["l"] >= hb["r"] or v["r"] <= hb["l"]), (v, hb)

        # C8 on the mousedown path: long-press accept does not accept.
        b.js("draftStudio.view.dispatch({selection: {anchor: 0}})")
        propose(b, tap=True)
        n = b.js("document.querySelectorAll('.cm-hunk').length")
        b.touch(".cm-hunk .hunk-accept", 0.8, lambda: bubble(b, EN["tips"]["acceptHunk"]))
        no_bubble(b)
        time.sleep(0.4)
        assert b.js("document.querySelectorAll('.cm-hunk').length") == n
        b.touch(".cm-hunk .hunk-accept", 0.05)
        b.wait(f"document.querySelectorAll('.cm-hunk').length === {n - 1}", what="tap accepts")
        assert b.errors() == []
    finally:
        b.close()


@pytest.mark.parametrize("width,height", [(390, 844), (1920, 1080)])
@pytest.mark.parametrize("rtl", [False, True])
def test_c12_bubble_inside_viewport(site: tuple[str, str, Path], width: int, height: int, rtl: bool) -> None:
    base, did, _ = site
    b = Browser(width, height)
    try:
        for url in ("/", f"/d/?id={did}"):
            b.goto(base + url)
            if rtl:
                b.js("document.documentElement.dir = 'rtl'")
            ids = b.js(
                "[...document.querySelectorAll('header .icon-btn')].filter(e => e.offsetParent)"
                ".map((e, i) => (e.dataset.tipTest = String(i)))"
            )
            for i in ids:
                b.hover(f"[data-tip-test='{i}']")
                v = bubble(b)
                assert inside(v), (url, i, v)
        assert b.errors() == []
    finally:
        b.close()
