"""GET /api/drafts/<id>/export.pdf: an approved draft as an A4 PDF, printed by headless chromium.

A fake chromium (a script written per test) stands in for the real one; the
e2e file tests/e2e/test_export.py prints with /usr/bin/chromium.
"""

from __future__ import annotations

import base64
import json
import stat
import sys
import urllib.request
from pathlib import Path

import pytest

from draft_studio import export
from draft_studio.store import Store
from tests.conftest import App
from tests.imgdata import png

FAKE = """#!{python}
import json, sys, time, pathlib
args = sys.argv[1:]
pathlib.Path({log!r}).write_text(json.dumps(args))
mode = {mode!r}
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    sys.exit(3)
out = next(a.split("=", 1)[1] for a in args if a.startswith("--print-to-pdf="))
page = pathlib.Path(args[-1].removeprefix("file://")).read_text(encoding="utf-8")
pathlib.Path(out).write_bytes(b"%PDF-1.7 fake\\n" + page.encode())
"""


def fake_chromium(tmp_path: Path, mode: str = "ok") -> tuple[Path, Path]:
    """An executable that answers like chromium --print-to-pdf; returns (binary, argv log)."""
    log = tmp_path / f"argv-{mode}.json"
    binary = tmp_path / f"chromium-{mode}"
    binary.write_text(FAKE.format(python=sys.executable, log=str(log), mode=mode))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary, log


def approved_text(app: App, text: str, kind: str = "text", subject: str = "") -> str:
    _, d = app.call("POST", "/api/drafts", {"kind": kind})
    did = str(d["id"])
    app.call("PUT", f"/api/drafts/{did}", {"text": text, "subject": subject, "phase": "editing"})
    assert app.call("POST", f"/api/drafts/{did}/approve", {"text": text})[0] == 200
    return did


def get_pdf(app: App, did: str) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(f"{app.base}/api/drafts/{did}/export.pdf")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


# -- the page chromium prints ------------------------------------------------


def test_document_is_a4_utf8_and_phone_readable() -> None:
    page = export.document("Instrukcja <dla> Anny", "pl", "# Tytuł\n\nTekst **ważny**.", lambda name: None)
    assert page.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in page
    assert '<html lang="pl">' in page
    assert "<title>Instrukcja &lt;dla&gt; Anny</title>" in page
    assert "size: A4" in page
    assert "max-width: 100%" in page and "break-inside: avoid" in page
    assert "<h1>Tytuł</h1>" in page and "<strong>ważny</strong>" in page


def test_images_are_inlined_as_data_uris() -> None:
    data = png()
    seen: list[str] = []

    def load(name: str) -> tuple[bytes, str]:
        seen.append(name)
        return data, "image/png"

    page = export.document(
        "t", "en", "![shot](assets/a.png)\n![](https://x.y/z.png)\n![](assets/../x.png)", load
    )
    uri = "data:image/png;base64," + base64.b64encode(data).decode()
    assert f'<img src="{uri}" alt="shot">' in page
    assert "x.y/z.png" not in page and "../x.png" not in page
    assert seen == ["a.png"]


@pytest.mark.parametrize(
    ("subject", "text", "want"),
    [
        ("Umowa", "# Inne\n", "Umowa"),
        ("", "\n\n# Instrukcja dla **Pani** Anny\nx", "Instrukcja dla Pani Anny"),
        ("", "Zwykły tekst [link](https://a.b)\n", "Zwykły tekst link"),
        ("  ", "![](assets/a.png)\n\n", ""),
    ],
)
def test_title_of(subject: str, text: str, want: str) -> None:
    assert export.title_of({"subject": subject}, text) == want


def test_disposition_keeps_polish_letters_and_an_ascii_fallback() -> None:
    header = export.disposition("Instrukcja: łódź/żółw? Ąę")
    header.encode("latin-1")  # BaseHTTPRequestHandler.send_header encodes latin-1 strictly
    assert header.startswith('attachment; filename="Instrukcja-lodz-zolw-Ae.pdf"; ')
    assert header.endswith(
        "filename*=UTF-8''Instrukcja-%C5%82%C3%B3d%C5%BA-%C5%BC%C3%B3%C5%82w-%C4%84%C4%99.pdf"
    )
    assert export.disposition("").startswith('attachment; filename="draft.pdf"')
    assert export.disposition("??? ***").startswith('attachment; filename="draft.pdf"')
    long = export.disposition("a" * 300)
    assert 'filename="' + "a" * 80 + '.pdf"' in long


# -- printing ------------------------------------------------------------------


def test_render_calls_chromium_headless_with_own_profile(tmp_path: Path) -> None:
    binary, log = fake_chromium(tmp_path)
    work = tmp_path / "data" / "export"
    pdf = export.render("<p>hi</p>", work, chromium=str(binary), timeout=10)
    assert pdf.startswith(b"%PDF-") and b"<p>hi</p>" in pdf
    args = json.loads(log.read_text())
    assert "--headless=new" in args and "--no-pdf-header-footer" in args and "--disable-extensions" in args
    profile = next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir="))
    assert Path(profile).is_relative_to(work)
    assert list(work.iterdir()) == []  # the throwaway dir is gone afterwards


def test_render_without_chromium_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(export.ExportError) as err:
        export.render("<p>hi</p>", tmp_path, chromium=str(tmp_path / "missing"), timeout=10)
    assert (err.value.code, err.value.status) == ("no-chromium", 503)


def test_render_timeout_kills_chromium(tmp_path: Path) -> None:
    binary, _ = fake_chromium(tmp_path, "sleep")
    with pytest.raises(export.ExportError) as err:
        export.render("<p>hi</p>", tmp_path / "w", chromium=str(binary), timeout=0.5)
    assert (err.value.code, err.value.status) == ("pdf-timeout", 504)
    assert list((tmp_path / "w").iterdir()) == []


def test_render_failure_is_named(tmp_path: Path) -> None:
    binary, _ = fake_chromium(tmp_path, "fail")
    with pytest.raises(export.ExportError) as err:
        export.render("<p>hi</p>", tmp_path / "w", chromium=str(binary), timeout=10)
    assert (err.value.code, err.value.status) == ("pdf-failed", 502)


def test_render_reads_binary_and_timeout_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary, _ = fake_chromium(tmp_path, "sleep")
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(binary))
    monkeypatch.setenv("DRAFT_STUDIO_PDF_TIMEOUT", "0.3")
    with pytest.raises(export.ExportError) as err:
        export.render("<p>hi</p>", tmp_path / "w")
    assert err.value.code == "pdf-timeout"


# -- the route -----------------------------------------------------------------


def test_unapproved_draft_is_refused(app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(fake_chromium(tmp_path)[0]))
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    status, _, body = get_pdf(app, str(d["id"]))
    assert (status, json.loads(body)) == (409, {"error": "not-approved"})


def test_edit_after_approval_is_refused(app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(fake_chromium(tmp_path)[0]))
    did = approved_text(app, "Hello")
    app.call("PUT", f"/api/drafts/{did}", {"text": "Hello!"})
    assert get_pdf(app, did)[0] == 409
    # A write that bypassed the store: the hash no longer matches.
    did = approved_text(app, "Hello")
    (app.store.root / "drafts" / did / "draft.md").write_text("tampered", encoding="utf-8")
    status, _, body = get_pdf(app, did)
    assert (status, json.loads(body)) == (409, {"error": "stale"})


def test_approved_draft_downloads_as_pdf(app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(fake_chromium(tmp_path)[0]))
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = str(d["id"])
    name = app.store.add_asset(did, "shot.png", png())
    text = f"# Instrukcja dla Pani Anny\n\nKrok 1.\n\n![](assets/{name})\n"
    app.call("PUT", f"/api/drafts/{did}", {"text": text, "phase": "editing"})
    assert app.call("POST", f"/api/drafts/{did}/approve", {"text": text})[0] == 200
    status, headers, body = get_pdf(app, did)
    assert status == 200
    assert headers["Content-Type"] == "application/pdf"
    assert headers["Content-Disposition"].startswith('attachment; filename="Instrukcja-dla-Pani-Anny.pdf"')
    assert body.startswith(b"%PDF-") and b"data:image/png;base64," in body
    assert list((app.store.root / "export").iterdir()) == []


def test_mail_title_comes_from_the_subject(app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(fake_chromium(tmp_path)[0]))
    did = approved_text(app, "Body", kind="mail", subject="Spotkanie w piątek")
    _, headers, _ = get_pdf(app, did)
    assert "filename*=UTF-8''Spotkanie-w-pi%C4%85tek.pdf" in headers["Content-Disposition"]


def test_missing_chromium_is_a_named_error(app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(tmp_path / "none"))
    did = approved_text(app, "Hello")
    status, _, body = get_pdf(app, did)
    assert (status, json.loads(body)) == (503, {"error": "no-chromium"})


def test_unknown_draft_and_other_methods(app: App) -> None:
    assert get_pdf(app, "zzzzzzzz")[0] == 404
    assert app.call("POST", "/api/drafts/zzzzzzzz/export.pdf", {})[0] == 404


def test_blockquote_is_printed_as_an_indented_quote_without_a_marker() -> None:
    page = export.document("t", "pl", "Wzór:\n> Dzień dobry,\n>\n> pozdrawiam", lambda name: None)
    assert "<blockquote>\n<p>Dzień dobry,</p>\n<p>pozdrawiam</p>\n</blockquote>" in page
    assert "&gt;" not in page
    assert "blockquote {" in export.CSS and "border-left:" in export.CSS


def test_store_work_dir_is_under_the_data_dir(store: Store) -> None:
    assert export.work_dir(store.root) == store.root / "export"
