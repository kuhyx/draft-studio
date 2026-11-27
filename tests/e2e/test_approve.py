"""Approve toggle: one click approves the shown text on disk, an edit drops it, a second click undoes it.

Run: .venv/bin/pytest -m e2e tests/e2e/test_approve.py (needs web/dist built and chromium).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.cdp import Browser, free_port, scratch_dir

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "web" / "dist"
PRESSED = "document.querySelector('#approve').getAttribute('aria-pressed')"


class Studio:
    def __init__(self, base: str, data: Path, b: Browser, shots: Path) -> None:
        self.base, self.data, self.b, self.shots = base, data, b, shots

    def api(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def hook_allows(self, draft_id: str) -> bool:
        """The outbound hook's check on the files (see tests/test_approval.py)."""
        folder = self.data / "drafts" / draft_id
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256((folder / "draft.md").read_bytes()).hexdigest()
        return meta.get("phase") == "approved" and meta.get("approvedSha256") == digest


@pytest.fixture(scope="module")
def studio() -> Iterator[Studio]:
    assert (DIST / "index.html").is_file(), "build web/dist first"
    tmp = scratch_dir("ds-e2e-approve-")
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


def test_approve_edit_drops_it(studio: Studio) -> None:
    b = studio.b
    draft_id = str(studio.api("POST", "/api/drafts", {"kind": "mail"})["id"])
    b.goto(f"{studio.base}/d/?id={draft_id}")
    assert b.js("document.querySelector('#approve').hidden") is True  # nothing to approve during the brief
    studio.api("PUT", f"/api/drafts/{draft_id}", {"text": "Hello Anna,\nsee you Friday.", "phase": "editing"})
    b.goto(f"{studio.base}/d/?id={draft_id}")
    b.wait("!document.querySelector('#work').hidden", what="editor")
    assert b.js(PRESSED) == "false"
    # Type, then approve at once: the click saves first, so the approved hash covers the new text.
    b.js(
        "draftStudio.view.focus(); draftStudio.view.dispatch({selection: {anchor: draftStudio.view.state.doc.length}})"
    )
    b.type(" Bye.")
    b.click("#approve")
    b.wait(f"{PRESSED} === 'true'", what="approved")
    assert b.js("document.querySelector('#approve').dataset.icon") == "approved"
    assert b.js("document.querySelector('#approve').innerText.trim()") == ""
    state = studio.api("GET", f"/api/drafts/{draft_id}")
    assert state["approved"] is True and state["text"].endswith("Friday. Bye.")
    assert studio.hook_allows(draft_id)
    assert not b.js("document.querySelector('#work').hidden")  # still editable while approved
    b.screenshot(studio.shots / "approve-on.png")
    # Reload keeps the state.
    b.goto(f"{studio.base}/d/?id={draft_id}")
    b.wait(f"{PRESSED} === 'true'", what="approved after reload")
    # Any edit drops the approval, on disk and on the button.
    b.js("draftStudio.view.focus(); draftStudio.view.dispatch({selection: {anchor: 0}})")
    b.type("Hi! ")
    b.wait(f"{PRESSED} === 'false'", what="approval dropped")
    assert b.js("document.querySelector('#approve').dataset.icon") == "approve"
    assert studio.api("GET", f"/api/drafts/{draft_id}")["meta"]["phase"] == "editing"
    assert not studio.hook_allows(draft_id)
    # Approve again, then take it back with a second click.
    b.click("#approve")
    b.wait(f"{PRESSED} === 'true'", what="approved again")
    b.click("#approve")
    b.wait(f"{PRESSED} === 'false'", what="unapproved")
    assert not studio.hook_allows(draft_id)
    kinds = [h["type"] for h in studio.api("GET", f"/api/drafts/{draft_id}/history")["versions"]]
    assert kinds.count("approve") == 2 and kinds[-1] == "unapprove"
