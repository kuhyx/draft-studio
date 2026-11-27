"""PDF export button: disabled until approved, one click downloads a real PDF, an edit disables it again.

Run: .venv/bin/pytest -m e2e tests/e2e/test_export.py (needs web/dist built, chromium and poppler).
Scratch data, downloads and page renders go under $DS_E2E_DIR (default
~/data/claude-scratch/draft-studio-e2e), never /tmp; screenshots to $DS_SHOTS.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
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
SCRATCH = Path(os.environ.get("DS_E2E_DIR") or Path.home() / "data" / "claude-scratch" / "draft-studio-e2e")
PDF_BTN = "document.querySelector('#export-pdf')"
TEXT = "\n\n".join(
    [
        "# Instrukcja dla Pani Anny",
        "Ten tekst sprawdza eksport do PDF: **pogrubienie**, *kursywa* i [link](https://example.com).",
        *(
            f"## Krok {n}\n\nZaloguj się i kliknij przycisk numer {n}. Zażółć gęślą jaźń."
            for n in range(1, 9)
        ),
        "![](assets/{shot})",
        "- punkt pierwszy\n- punkt drugi",
    ]
)


class Studio:
    def __init__(self, base: str, root: Path, b: Browser, shots: Path) -> None:
        self.base, self.root, self.b, self.shots = base, root, b, shots

    def api(self, method: str, path: str, body: Any = None, raw: bytes | None = None) -> Any:
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "image/png" if raw is not None else "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def status(self, path: str) -> int:
        try:
            with urllib.request.urlopen(self.base + path, timeout=90) as r:
                return int(r.status)
        except urllib.error.HTTPError as err:
            return err.code


@pytest.fixture(scope="module")
def studio() -> Iterator[Studio]:
    assert (DIST / "index.html").is_file(), "build web/dist first"
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="export-", dir=SCRATCH))
    shots = Path(os.environ.get("DS_SHOTS", root / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    environ = {
        **{k: v for k, v in os.environ.items() if k != "DRAFT_STUDIO_CHROMIUM"},
        "DRAFT_STUDIO_DATA": str(root / "data"),
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
        yield Studio(base, root, b, shots)
        assert b.errors() == [], b.errors()
    finally:
        b.close()
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(root / "data", ignore_errors=True)


def wait_file(folder: Path, suffix: str, timeout: float = 90) -> Path:
    deadline = time.time() + timeout
    while time.time() < deadline:
        done = [p for p in folder.glob(f"*{suffix}") if p.stat().st_size]
        if done and not list(folder.glob("*.crdownload")):
            return done[0]
        time.sleep(0.1)
    raise AssertionError(f"no {suffix} in {folder}")


def test_pdf_export(studio: Studio) -> None:
    b = studio.b
    did = str(studio.api("POST", "/api/drafts", {"kind": "text"})["id"])
    shot = studio.api("POST", f"/api/drafts/{did}/assets?name=shot.png", raw=png(640, 900))["name"]
    studio.api("PUT", f"/api/drafts/{did}", {"text": TEXT.replace("{shot}", shot), "phase": "editing"})
    b.goto(f"{studio.base}/d/?id={did}")
    b.wait("!document.querySelector('#work').hidden", what="editor")
    # Not approved: shown, but disabled for clicks and screen readers, and the route refuses.
    assert b.js(f"{PDF_BTN}.hidden") is False
    assert b.js(f"{PDF_BTN}.disabled") is True
    assert b.js(f"{PDF_BTN}.getAttribute('aria-disabled')") == "true"
    assert b.js(f"{PDF_BTN}.getAttribute('aria-label')") == "PDF"
    assert b.js(f"{PDF_BTN}.innerText.trim()") == ""
    assert studio.status(f"/api/drafts/{did}/export.pdf") == 409
    b.screenshot(studio.shots / "export-off.png")

    b.click("#approve")
    b.wait(f"!{PDF_BTN}.disabled", what="export enabled")
    assert b.js(f"{PDF_BTN}.getAttribute('aria-disabled')") == "false"
    b.screenshot(studio.shots / "export-on.png")

    downloads = studio.root / "downloads"
    downloads.mkdir(exist_ok=True)
    b.call("Browser.setDownloadBehavior", behavior="allow", downloadPath=str(downloads))
    b.click("#export-pdf")
    pdf = wait_file(downloads, ".pdf")
    assert pdf.name == "Instrukcja-dla-Pani-Anny.pdf"
    assert pdf.read_bytes().startswith(b"%PDF-")
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    pages = int(next(line.split()[-1] for line in info.splitlines() if line.startswith("Pages:")))
    assert pages >= 2, info
    assert "pts (A4)" in info
    subprocess.run(
        ["pdftoppm", "-png", "-r", "60", "-f", "1", "-l", "1", str(pdf), str(studio.shots / "export-page")],
        check=True,
    )
    assert list(studio.shots.glob("export-page*.png"))
    shutil.copy(pdf, studio.shots / pdf.name)

    # An edit drops the approval: the button goes back to disabled.
    b.js("draftStudio.view.focus(); draftStudio.view.dispatch({selection: {anchor: 0}})")
    b.type("Hej! ")
    b.wait(
        f"{PDF_BTN}.disabled && {PDF_BTN}.getAttribute('aria-disabled') === 'true'", what="export disabled"
    )
    assert studio.status(f"/api/drafts/{did}/export.pdf") == 409
    assert list((studio.root / "data" / "export").iterdir()) == []  # every render cleaned up
