"""Gmail button: refused until approved, an edit drops the approval, images go inline.

Run: .venv/bin/pytest -m e2e tests/e2e/test_gmail.py (needs web/dist built and chromium).
Fake Gmail: the mailbox is the JSON file the server writes next to the drafts.
"""

from __future__ import annotations

import hashlib
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
SCRATCH = Path(os.environ.get("DS_E2E_TMP") or Path.home() / "data/claude-scratch/draft-studio-e2e")
STATUS = "document.querySelector('#status').dataset.status"


class Studio:
    def __init__(self, base: str, data: Path, b: Browser) -> None:
        self.base, self.data, self.b = base, data, b

    def api(self, method: str, path: str, body: Any = None, raw: bytes | None = None) -> Any:
        data = raw if raw is not None else None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "image/png" if raw is not None else "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def box(self, did: str = "") -> dict[str, Any]:
        """The fake mailbox; with did, only the drafts that draft wrote."""
        f = self.data / "gmail-fake.json"
        box: dict[str, Any] = json.loads(f.read_text()) if f.exists() else {}
        return {k: v for k, v in box.items() if not did or v["studioId"] == did}

    def open_mail(self, text: str, image: bytes | None = None) -> tuple[str, str]:
        did = str(self.api("POST", "/api/drafts", {"kind": "mail"})["id"])
        name = ""
        if image is not None:
            name = self.api("POST", f"/api/drafts/{did}/assets?name=chart.png", raw=image)["name"]
            text = text.replace("NAME", name)
        fields = {"text": text, "to": "ann@example.com", "subject": "Hi", "phase": "editing"}
        self.api("PUT", f"/api/drafts/{did}", {**fields, "toAuthor": "you", "subjectAuthor": "you"})
        self.b.goto(f"{self.base}/d/?id={did}")
        self.b.wait("document.body.dataset.ready === '1' && !!window.draftStudio", what="editor ready")
        return did, name

    def approve(self, did: str) -> None:
        text = self.api("GET", f"/api/drafts/{did}")["text"]
        assert self.api("POST", f"/api/drafts/{did}/approve", {"text": text})["approved"] is True

    def press(self, want: str) -> None:
        self.b.js(f"{STATUS} = ''")
        self.b.click("#gmail")
        self.b.wait(f"{STATUS} === '{want}'", what=f"gmail {want}")


@pytest.fixture(scope="module")
def studio() -> Iterator[Studio]:
    assert (DIST / "index.html").is_file(), "build web/dist first"
    SCRATCH.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="gmail-", dir=SCRATCH))
    data = tmp / "data"
    port = free_port()
    environ = {**os.environ, "DRAFT_STUDIO_DATA": str(data), "DRAFT_STUDIO_CLAUDE": "fake"}
    environ["DRAFT_STUDIO_GMAIL"] = "fake"
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
        yield Studio(base, data, b)
        assert b.errors() == [], b.errors()
    finally:
        b.close()
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


def test_c24_c25_gmail_needs_approval_then_updates(studio: Studio) -> None:
    did, _ = studio.open_mail("Hi Ann,\n**see** you.")
    studio.press("error")  # not approved yet: nothing reaches Gmail
    assert studio.box() == {}
    studio.approve(did)
    studio.press("done")
    box = studio.box()
    assert len(box) == 1
    gid, mail = next(iter(box.items()))
    assert mail["to"] == ["ann@example.com"] and mail["subject"] == "Hi" and mail["studioId"] == did
    assert mail["body"] == "Hi Ann,\nsee you."
    assert mail["htmlBody"] == "<p>Hi Ann,<br>\n<strong>see</strong> you.</p>"
    # An edit drops the approval: refused again, Gmail untouched.
    end = len(str(studio.b.js("draftStudio.view.state.doc.toString()")))
    studio.b.js(f"draftStudio.view.focus(); draftStudio.view.dispatch({{selection: {{anchor: {end}}}}})")
    studio.b.type(" More")
    studio.b.wait(f"{STATUS} === 'saved'", what="saved")
    assert studio.api("GET", f"/api/drafts/{did}")["approved"] is False
    studio.press("error")
    assert studio.box() == box
    studio.approve(did)
    studio.press("done")
    box = studio.box()
    assert list(box) == [gid] and box[gid]["body"].endswith(" More")
    # Deleted in Gmail: the next press creates a new one.
    (studio.data / "gmail-fake.json").write_text("{}")
    studio.press("done")
    assert len(studio.box()) == 1
    assert studio.api("GET", f"/api/drafts/{did}")["meta"]["gmailDraftId"] not in ("", gid)


def test_gmail_images_inline_and_replaced(studio: Studio) -> None:
    image = png(6, 4)
    did, name = studio.open_mail("Look:\n![chart](assets/NAME)", image)
    studio.approve(did)
    studio.press("done")
    ((gid, mail),) = studio.box(did).items()
    path = (studio.data / "drafts" / did / "assets" / name).resolve()
    assert mail["attachments"] == [
        {
            "content": f"sha256:{hashlib.sha256(image).hexdigest()}:{path}",
            "filename": name,
            "mimeType": "image/png",
            "inline": True,
        }
    ]
    assert (
        mail["htmlBody"] == '<p>Look:<br>\n<img src="cid:att0" alt="chart"></p>' and mail["body"] == "Look:"
    )
    # Files cannot survive update_draft: a new draft replaces the stored one.
    studio.press("done")
    ((new_id, _),) = studio.box(did).items()
    assert new_id != gid
    assert studio.api("GET", f"/api/drafts/{did}")["meta"]["gmailDraftId"] == new_id
