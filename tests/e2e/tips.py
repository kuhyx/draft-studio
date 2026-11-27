"""Shared probes of the tooltip tests: the bubble, element geometry, selection and viewport switch."""

from __future__ import annotations

import json
from typing import Any

from tests.e2e.cdp import Browser
from tests.e2e.env import REPO

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


def viewport(b: Browser, width: int, height: int, mobile: bool = False) -> None:
    """Resize the one shared browser (and switch touch emulation) instead of launching another."""
    b.call(
        "Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=mobile
    )
    b.call("Emulation.setTouchEmulationEnabled", enabled=mobile, maxTouchPoints=5)


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


def select(b: Browser, needle: str) -> None:
    i = TEXT.index(needle)
    b.js(
        f"draftStudio.view.focus(); draftStudio.view.dispatch({{selection: {{anchor: {i}, head: {i + len(needle)}}}}})"
    )
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
