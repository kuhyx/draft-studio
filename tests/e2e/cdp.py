"""Minimal Chrome DevTools Protocol driver for the headless E2E flow.

Own chromium, own profile, own port (never 9222, never kuhy's display).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket

FALLBACKS = (
    "/usr/bin/chromium",
    *sorted(
        (str(p) for p in Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome")),
        key=lambda p: int(p.split("chromium-")[1].split("/")[0]),
        reverse=True,
    ),
)


def chromium() -> str:
    """$DS_CHROMIUM, else the first browser that actually starts (system chromium can lose a .so)."""
    explicit = os.environ.get("DS_CHROMIUM")
    if explicit:
        return explicit
    for path in FALLBACKS:
        probe = subprocess.run([path, "--version"], capture_output=True, check=False)
        if probe.returncode == 0:
            return path
    raise RuntimeError("no working chromium")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class Browser:
    """Headless chromium with one page and a CDP session on it."""

    def __init__(self, width: int = 1920, height: int = 1080, mobile: bool = False) -> None:
        self.port = free_port()
        self.profile = Path(tempfile.mkdtemp(prefix="ds-e2e-profile-"))
        self.proc = subprocess.Popen(
            [
                chromium(),
                "--headless=new",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile}",
                f"--window-size={width},{height}",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.ws = websocket.create_connection(self._page_ws(), suppress_origin=True, timeout=30)
        self._id = 0
        self.events: list[dict[str, Any]] = []
        self.call("Runtime.enable")
        self.call("Page.enable")
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=1,
            mobile=mobile,
        )
        if mobile:
            self.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)

    def _page_ws(self) -> str:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
                    for t in json.loads(r.read()):
                        if t.get("type") == "page":
                            url: str = t["webSocketDebuggerUrl"]
                            return url
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError("chromium did not start")

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self._id += 1
        my = self._id
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                result: dict[str, Any] = msg.get("result", {})
                return result
            self.events.append(msg)

    def js(self, expr: str) -> Any:
        out = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in out:
            raise RuntimeError(f"js failed: {expr[:120]}: {out['exceptionDetails']}")
        return out.get("result", {}).get("value")

    def wait(self, expr: str, timeout: float = 10.0, what: str = "") -> Any:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                value = self.js(expr)
            except RuntimeError:  # page mid-navigation: try again
                value = None
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(f"timeout waiting for {what or expr}")

    def goto(self, url: str) -> None:
        self.js("window.__oldPage = true")
        self.call("Page.navigate", url=url)
        self.wait("!window.__oldPage && document.body?.dataset.ready === '1'", what=f"load {url}")

    def _settled(self, selector: str, scroll: bool = False) -> list[float]:
        """Centre of a visible element once it held still across rendered frames.

        Each probe first waits two animation frames, so CodeMirror's measure pass
        (a ResizeObserver, then a requestMeasure on the next frame) has run before
        the rect is read. Wall-clock gaps are not enough: under the CPU cap two
        probes 50 ms apart can both land between the same pair of frames, see the
        pre-measure layout as "stable", and the click then hits the moved toolbar.
        """
        scroll_js = " e.scrollIntoView({block: 'nearest'});" if scroll else ""
        probe = (
            "new Promise(ok => requestAnimationFrame(() => requestAnimationFrame(() => {"
            f" const e = document.querySelector({json.dumps(selector)}); if (!e) return ok(null);{scroll_js}"
            " const r = e.getBoundingClientRect(); ok(r.width ? [r.x + r.width / 2, r.y + r.height / 2] : null)"
            " })))"
        )
        rect = self.wait(probe, what=selector)
        for _ in range(40):
            again = self.wait(probe, what=selector)
            if again == rect:
                return list(rect)
            rect = again
        raise AssertionError(f"{selector} kept moving")

    def click(self, selector: str) -> None:
        """A real mouse click at the element's centre, once it stopped moving."""
        x, y = self._settled(selector, scroll=True)
        hit = self.js(
            f"(() => {{ const e = document.querySelector({json.dumps(selector)});"
            f" const h = document.elementFromPoint({x}, {y}); return !!h && (e === h || e.contains(h)) }})()"
        )
        if not hit:
            cover = self.js(f"document.elementFromPoint({x}, {y})?.outerHTML.slice(0, 300)")
            raise AssertionError(f"{selector} is covered at {[x, y]} by {cover}")
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def center(self, selector: str) -> list[float]:
        """Centre of a visible element, once it stops moving."""
        return self._settled(selector)

    def hover(self, selector: str) -> None:
        """Move the mouse onto the element's centre."""
        x, y = self.center(selector)
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    def mouse_to(self, x: float, y: float) -> None:
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    def touch(self, selector: str, hold: float, during: Any = None) -> Any:
        """Press a finger on the element for `hold` seconds; `during()` runs before the lift."""
        x, y = self.center(selector)
        point = [{"x": x, "y": y}]
        self.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=point)
        time.sleep(hold)
        seen = during() if during else None
        self.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
        return seen

    def type(self, text: str) -> None:
        self.call("Input.insertText", text=text)

    def key(self, key: str, code: str, vk: int, modifiers: int = 0) -> None:
        for kind in ("rawKeyDown", "keyUp"):
            self.call(
                "Input.dispatchKeyEvent",
                type=kind,
                key=key,
                code=code,
                windowsVirtualKeyCode=vk,
                modifiers=modifiers,
            )

    def screenshot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))

    def errors(self) -> list[str]:
        out = []
        for e in self.events:
            if e.get("method") == "Runtime.exceptionThrown":
                out.append(json.dumps(e["params"]["exceptionDetails"])[:300])
            if e.get("method") == "Runtime.consoleAPICalled" and e["params"]["type"] == "error":
                out.append(json.dumps(e["params"]["args"])[:300])
        return out

    def close(self) -> None:
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            shutil.rmtree(self.profile, ignore_errors=True)
