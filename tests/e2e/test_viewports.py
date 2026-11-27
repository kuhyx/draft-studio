"""R1 viewport matrix: list and editor pages at five sizes, LTR and RTL.

Per size: no sideways scroll, no [object Object]/undefined/NaN text, every
primary control inside the viewport and not covered. Screenshots: $DS_SHOTS.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.cdp import Browser, free_port, scratch_dir

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
SIZES = [(3840, 2160), (2560, 1440), (1920, 1080), (1366, 768), (390, 844)]
CHECK = """((sel) => {
  const out = [];
  const de = document.documentElement;
  if (de.scrollWidth > innerWidth + 1) out.push(`overflow-x ${de.scrollWidth} > ${innerWidth}`);
  const text = document.body.innerText;
  for (const bad of ['[object Object]', 'undefined', 'NaN']) if (text.includes(bad)) out.push('bad text ' + bad);
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest('[hidden]')) continue;
    const r = el.getBoundingClientRect();
    const name = el.id || el.dataset.label || el.className;
    if (r.bottom > innerHeight || r.right > innerWidth || r.top < 0 || r.left < 0) { out.push('off screen ' + name); continue; }
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !(hit === el || el.contains(hit))) out.push('covered ' + name);
  }
  return out;
})"""
CONTROLS = "header .icon-btn, header input, #to, #subject, .draft-row .delete, .cm-content, .ask-card"


def post(base: str, path: str, body: object, method: str = "POST") -> dict[str, object]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        out: dict[str, object] = json.loads(r.read())
        return out


@pytest.fixture(scope="module")
def site() -> Iterator[tuple[str, str, Path]]:
    tmp = scratch_dir("ds-vp-")
    shots = Path(os.environ.get("DS_SHOTS", tmp / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(tmp / "data"),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_GMAIL": "fake",
    }
    server = subprocess.Popen([sys.executable, "-m", "draft_studio", "--port", str(port)], cwd=REPO, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/version", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    mail = post(base, "/api/drafts", {"kind": "mail"})
    did = str(mail["id"])
    post(base, f"/api/drafts/{did}/brief", {"brief": "A long mail to the owner about the website [noq]"})
    text = "Dear owner,\n\n" + "This is a long paragraph of the draft that wraps across lines. " * 12
    post(
        base,
        f"/api/drafts/{did}",
        {"text": text, "claude": [[0, 40]], "to": "owner@example.com", "subject": "About the website"},
        method="PUT",
    )
    post(base, f"/api/drafts/{did}/ask", {"text": text, "from": 0, "to": 10, "question": "Too formal?"})
    post(base, "/api/drafts", {"kind": "text"})
    yield base, did, shots
    server.terminate()
    server.wait(timeout=10)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("width,height", SIZES)
@pytest.mark.parametrize("rtl", [False, True])
def test_viewports(site: tuple[str, str, Path], width: int, height: int, rtl: bool) -> None:
    base, did, shots = site
    b = Browser(width, height)
    try:
        for name, url in (("list", "/"), ("editor", f"/d/?id={did}")):
            b.goto(base + url)
            if rtl:
                b.js("document.documentElement.dir = 'rtl'")
            time.sleep(0.2)
            problems = b.js(f"{CHECK}({json.dumps(CONTROLS)})")
            b.screenshot(shots / f"vp-{name}-{width}x{height}{'-rtl' if rtl else ''}.png")
            assert problems == [], f"{name} {width}x{height} rtl={rtl}: {problems}"
        assert b.errors() == []
    finally:
        b.close()
