"""Approved draft -> PDF: an A4 HTML page printed by headless chromium.

The markdown goes through mailhtml (the same subset the Gmail draft gets), with
stored images inlined as data URIs, so chromium loads nothing from disk or the
network. The print CSS targets reading on a phone: a large body font, images
no wider than the page and never split across pages.

Each render gets its own throwaway dir under the data dir (page, PDF and the
chromium profile), removed afterwards: never /tmp, never kuhy's profile.
"""

from __future__ import annotations

import base64
import contextlib
import html
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from draft_studio import mailhtml
from draft_studio.store import ASSET_NAME_RE

LOG = logging.getLogger("draft_studio")
# /usr/local/bin/chromium is a hosts-file wrapper on kuhy's box: use the real binary.
CHROMIUM = "/usr/bin/chromium"
# Generous: the e2e suite runs chromium at 10 % of one CPU.
TIMEOUT_S = 60.0
_NAME_MAX = 80
_ASCII = str.maketrans({"ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "đ": "d"})
_PLAIN = re.compile(r"!\[[^\]\n]*\]\([^)\s]+\)|\[([^\]\n]+)\]\([^)\s]+\)|[*_`]+")
ImageLoader = Callable[[str], tuple[bytes, str]]

CSS = """
@page { size: A4; margin: 10mm; }
html { font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 18pt; line-height: 1.45; color: #111; }
body { margin: 0; overflow-wrap: anywhere; }
h1 { font-size: 26pt; } h2 { font-size: 22pt; } h3, h4, h5, h6 { font-size: 19pt; }
h1, h2, h3, h4, h5, h6 {
  line-height: 1.2; margin: 1.1em 0 0.4em; break-after: avoid; page-break-after: avoid;
}
p, ul, ol { margin: 0 0 0.6em; }
a { color: #0b57d0; }
code, pre { font-family: "Noto Sans Mono", "DejaVu Sans Mono", monospace; font-size: 0.85em; }
pre { white-space: pre-wrap; background: #f3f3f3; padding: 3mm; border-radius: 2mm; }
img {
  display: block; max-width: 100%; height: auto; max-height: 260mm; margin: 3mm auto;
  border: 0.3mm solid #ccc; break-inside: avoid; page-break-inside: avoid;
}
"""


class ExportError(RuntimeError):
    """The PDF could not be made; `code` is the API error code, `status` its HTTP status."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _data_uris(load: ImageLoader) -> mailhtml.ImgSrc:
    """Stored images (assets/<name>) as data URIs; any other src is dropped."""

    def src(ref: str) -> str | None:
        name = ref.removeprefix("assets/")
        if name == ref or not ASSET_NAME_RE.match(name):
            return None
        data, mime = load(name)
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    return src


def document(title: str, lang: str, markdown: str, load: ImageLoader) -> str:
    """The whole HTML page chromium prints."""
    body = mailhtml.html_body(markdown, _data_uris(load))
    return (
        "<!doctype html>\n"
        f'<html lang="{html.escape(lang or "und")}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def title_of(meta: dict[str, Any], text: str) -> str:
    """The subject, else the first line of text (heading marks and inline markup removed)."""
    subject = str(meta.get("subject") or "").strip()
    if subject:
        return subject
    for line in text.split("\n"):
        plain = _PLAIN.sub(lambda m: m.group(1) or "", line.lstrip("#").strip()).strip()
        if plain:
            return plain
    return ""


def disposition(title: str) -> str:
    """Content-Disposition for the download: an ASCII filename plus the UTF-8 one (RFC 6266)."""
    stem = "-".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFC", title)))[:_NAME_MAX] or "draft"
    ascii_stem = unicodedata.normalize("NFKD", stem.translate(_ASCII)).encode("ascii", "ignore").decode()
    ascii_stem = re.sub(r"-{2,}", "-", ascii_stem).strip("-") or "draft"
    return f"attachment; filename=\"{ascii_stem}.pdf\"; filename*=UTF-8''{quote(stem, safe='-')}.pdf"


def work_dir(data_root: Path) -> Path:
    """Where renders put their throwaway dirs."""
    return data_root / "export"


def render(page: str, work: Path, chromium: str | None = None, timeout: float | None = None) -> bytes:
    """Print `page` to PDF bytes with headless chromium, or raise a named ExportError."""
    binary = chromium or os.environ.get("DRAFT_STUDIO_CHROMIUM") or CHROMIUM
    limit = timeout if timeout is not None else float(os.environ.get("DRAFT_STUDIO_PDF_TIMEOUT") or TIMEOUT_S)
    if not os.access(binary, os.X_OK):
        LOG.error("pdf export: no chromium at %s", binary)
        raise ExportError("no-chromium", 503)
    work.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pdf-", dir=work))
    try:
        (tmp / "page.html").write_text(page, encoding="utf-8")
        out = tmp / "out.pdf"
        args = [
            binary,
            "--headless=new",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out}",
            f"--user-data-dir={tmp / 'profile'}",
            "--disable-extensions",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            (tmp / "page.html").as_uri(),
        ]
        # Own session: on timeout the whole group (renderer, GPU, zygote) dies, not just the parent.
        proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True
        )
        try:
            _, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            LOG.error("pdf export: chromium timed out after %ss", limit)
            raise ExportError("pdf-timeout", 504) from None
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        data = out.read_bytes() if out.is_file() else b""
        if proc.returncode != 0 or not data.startswith(b"%PDF-"):
            LOG.error(
                "pdf export: chromium exit %s: %s", proc.returncode, err.decode(errors="replace")[-500:]
            )
            raise ExportError("pdf-failed", 502)
        return data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
