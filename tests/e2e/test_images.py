"""Images in the editor: paste and drop upload, the <img> renders under its line, broken refs show an icon.

Run: .venv/bin/pytest -m e2e tests/e2e/test_images.py (needs web/dist built and chromium).
"""

from __future__ import annotations

import base64
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
from tests.imgdata import png

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "web" / "dist"


class Studio:
    def __init__(self, base: str, data: Path, b: Browser, shots: Path) -> None:
        self.base, self.data, self.b, self.shots = base, data, b, shots

    def api(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def open_draft(self, text: str) -> str:
        draft_id = str(self.api("POST", "/api/drafts", {"kind": "text"})["id"])
        self.api("PUT", f"/api/drafts/{draft_id}", {"text": text, "phase": "editing"})
        self.b.goto(f"{self.base}/d/?id={draft_id}")
        self.b.wait("document.body.dataset.ready === '1' && !!window.draftStudio", what="editor ready")
        return draft_id

    def doc(self) -> str:
        return str(self.b.js("draftStudio.view.state.doc.toString()"))

    def file_event(self, kind: str, data: bytes, name: str, pos: int) -> None:
        """Fire a real paste/drop DOM event carrying one image File at doc position pos."""
        b64 = base64.b64encode(data).decode()
        self.b.js(
            f"""(() => {{
              const v = draftStudio.view
              v.focus()
              v.dispatch({{selection: {{anchor: {pos}}}}})
              const bytes = Uint8Array.from(atob('{b64}'), c => c.charCodeAt(0))
              const dt = new DataTransfer()
              dt.items.add(new File([bytes], '{name}', {{type: 'image/png'}}))
              if ('{kind}' === 'paste') {{
                v.contentDOM.dispatchEvent(new ClipboardEvent('paste', {{clipboardData: dt, bubbles: true, cancelable: true}}))
              }} else {{
                const r = v.coordsAtPos({pos})
                v.contentDOM.dispatchEvent(new DragEvent('drop', {{dataTransfer: dt, clientX: r.left + 1, clientY: (r.top + r.bottom) / 2, bubbles: true, cancelable: true}}))
              }}
            }})()"""
        )

    def saved(self, draft_id: str) -> Any:
        self.b.wait(
            "document.querySelector('#status').dataset.status === 'saved' && !document.body.dataset.busy",
            what="saved",
        )
        return self.api("GET", f"/api/drafts/{draft_id}")


@pytest.fixture(scope="module")
def studio() -> Iterator[Studio]:
    assert (DIST / "index.html").is_file(), "build web/dist first"
    tmp = Path(tempfile.mkdtemp(prefix="ds-e2e-img-"))
    data = tmp / "data"
    shots = Path(os.environ.get("DS_SHOTS", tmp / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    environ = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(data),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_GMAIL": "fake",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "draft_studio", "--port", str(port), "--build", "e2e"], cwd=REPO, env=environ
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/version", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    b = Browser()
    try:
        yield Studio(base, data, b, shots)
        assert b.errors() == [], b.errors()
    finally:
        b.close()
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


IMG_UNDER_LINE = """(() => {
  const line = [...document.querySelectorAll('.cm-line')].find(l => l.textContent.includes(%s))
  const box = line && line.nextElementSibling
  const img = box && box.querySelector('img')
  if (!img || !img.complete || !img.naturalWidth) return null
  const lr = line.getBoundingClientRect(), ir = img.getBoundingClientRect()
  const content = document.querySelector('.cm-content').getBoundingClientRect()
  return {cls: box.className, top: ir.top, lineBottom: lr.bottom, w: ir.width, contentW: content.width,
          natural: img.naturalWidth, src: img.getAttribute('src')}
})()"""


def test_paste_and_drop_upload_and_render(studio: Studio) -> None:
    draft_id = studio.open_draft("Intro line\nSecond line")
    studio.file_event(
        "paste", png(2400, 120, (30, 120, 200)), "wide shot.png", len("Intro line\nSecond line")
    )
    studio.b.wait("draftStudio.view.state.doc.toString().includes('](assets/')", what="markdown inserted")
    name = studio.doc().split("](assets/")[1].split(")")[0]
    assert name.startswith("wide-shot-") and name.endswith(".png")
    assert studio.doc() == f"Intro line\nSecond line\n![](assets/{name})\n"
    got = studio.b.wait(IMG_UNDER_LINE % json.dumps(name), what="img rendered under its line")
    assert got["cls"] == "cm-image"
    assert got["top"] >= got["lineBottom"] - 1
    assert got["natural"] == 2400
    assert 0 < got["w"] <= got["contentW"]  # scaled down to the editor width
    assert got["src"] == f"/api/drafts/{draft_id}/assets/{name}"
    studio.b.screenshot(studio.shots / "img-paste.png")

    # Drop a second image onto the start of the first line.
    studio.file_event("drop", png(40, 30, (220, 40, 40)), "dot.png", 0)
    studio.b.wait(
        "draftStudio.view.state.doc.toString().split('](assets/').length === 3", what="drop inserted"
    )
    first = studio.doc().splitlines()[0]
    assert first.startswith("![](assets/dot-")
    studio.b.wait(IMG_UNDER_LINE % json.dumps(first[len("![](assets/") : -1]), what="dropped img rendered")

    state = studio.saved(draft_id)
    assert state["text"] == studio.doc()
    stored = sorted(p.name for p in (studio.data / "drafts" / draft_id / "assets").iterdir())
    assert len(stored) == 2 and name in stored


def test_broken_reference_shows_icon(studio: Studio) -> None:
    studio.open_draft("![lost](assets/missing-0.png)\nafter")
    studio.b.wait(
        "!!document.querySelector('.cm-image[data-broken] .cm-image-broken svg')", what="broken icon"
    )
    info = studio.b.js(
        "(() => { const b = document.querySelector('.cm-image-broken');"
        " return {label: b.getAttribute('aria-label'), text: b.closest('.cm-image').textContent} })()"
    )
    assert info["label"] and info["text"] == ""
