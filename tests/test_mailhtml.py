"""Draft markdown -> Gmail htmlBody + plain-text body + inline image attachments."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from pathlib import Path

import pytest

from draft_studio.mail import Mail, MailError
from draft_studio.mailhtml import MAX_ATTACH_BYTES, build, recipients
from draft_studio.store import AssetNotFound
from tests.imgdata import JPEG, png


def loader(tmp_path: Path, files: dict[str, bytes]) -> Callable[[str], tuple[Path, bytes, str]]:
    folder = tmp_path / "assets"
    folder.mkdir(exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)

    def load(name: str) -> tuple[Path, bytes, str]:
        path = folder / name
        if not path.is_file():
            raise AssetNotFound(name)
        mime = "image/jpeg" if name.endswith(".jpg") else "image/png"
        return path, path.read_bytes(), mime

    return load


def mail(text: str, tmp_path: Path, files: dict[str, bytes] | None = None) -> Mail:
    return build("a@b.c", "S", text, loader(tmp_path, files or {}))


def test_plain_paragraphs_and_line_breaks(tmp_path: Path) -> None:
    m = mail("Hi Ann,\nsee below.\n\nBest,\nkuhy", tmp_path)
    assert m.html == "<p>Hi Ann,<br>\nsee below.</p>\n<p>Best,<br>\nkuhy</p>"
    assert m.text == "Hi Ann,\nsee below.\n\nBest,\nkuhy"
    assert m.attachments == []
    assert m.to == ["a@b.c"] and m.subject == "S"


def test_html_is_escaped(tmp_path: Path) -> None:
    m = mail('<script>alert("x")</script> & co', tmp_path)
    assert "<script>" not in m.html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; co" in m.html
    assert m.text == '<script>alert("x")</script> & co'


def test_inline_markup(tmp_path: Path) -> None:
    m = mail("A **bold** and *it* and _em_ and `co*de*` and \\*lit\\* snake_case_name", tmp_path)
    assert m.html == (
        "<p>A <strong>bold</strong> and <em>it</em> and <em>em</em> and "
        "<code>co*de*</code> and *lit* snake_case_name</p>"
    )
    assert m.text == "A bold and it and em and co*de* and *lit* snake_case_name"


def test_links_only_safe_schemes(tmp_path: Path) -> None:
    m = mail("[site](https://x.org/a?b=1&c=2) [me](mailto:k@x.org) [bad](javascript:alert(1))", tmp_path)
    assert '<a href="https://x.org/a?b=1&amp;c=2">site</a>' in m.html
    assert '<a href="mailto:k@x.org">me</a>' in m.html
    assert "javascript" not in m.html
    assert m.text.startswith("site (https://x.org/a?b=1&c=2) me (mailto:k@x.org) bad")
    same = mail("[https://x.org](https://x.org)", tmp_path)
    assert same.text == "https://x.org"


def test_headings_lists_and_code(tmp_path: Path) -> None:
    text = "# Title\n\n- one\n* **two**\n\n1. first\n2) second\n\n```\n# not a heading\n- raw\n```\nafter"
    m = mail(text, tmp_path)
    assert m.html == (
        "<h1>Title</h1>\n<ul>\n<li>one</li>\n<li><strong>two</strong></li>\n</ul>\n"
        "<ol>\n<li>first</li>\n<li>second</li>\n</ol>\n<pre><code># not a heading\n- raw</code></pre>\n"
        "<p>after</p>"
    )
    assert m.text == "Title\n\n• one\n• two\n\n1. first\n2) second\n\n# not a heading\n- raw\nafter"


def test_list_then_paragraph_without_blank_line(tmp_path: Path) -> None:
    m = mail("- one\nafter", tmp_path)
    assert m.html == "<ul>\n<li>one</li>\n</ul>\n<p>after</p>"
    assert m.text == "• one\nafter"


def test_unclosed_fence_runs_to_the_end(tmp_path: Path) -> None:
    m = mail("```\ncode <b>", tmp_path)
    assert m.html == "<pre><code>code &lt;b&gt;</code></pre>"
    assert m.text == "code <b>"


def test_images_become_numbered_inline_attachments(tmp_path: Path) -> None:
    pic, photo = png(), JPEG
    text = "Look:\n![a chart](assets/chart-1.png)\n\n![](assets/photo.jpg) and ![again](assets/chart-1.png)"
    m = mail(text, tmp_path, {"chart-1.png": pic, "photo.jpg": photo})
    assert '<img src="cid:att0" alt="a chart">' in m.html
    assert '<img src="cid:att1" alt="">' in m.html
    assert '<img src="cid:att0" alt="again">' in m.html  # same file: one attachment, reused
    folder = (tmp_path / "assets").resolve()
    assert m.attachments == [
        {
            "content": f"sha256:{hashlib.sha256(pic).hexdigest()}:{folder / 'chart-1.png'}",
            "filename": "chart-1.png",
            "mimeType": "image/png",
            "inline": True,
        },
        {
            "content": f"sha256:{hashlib.sha256(photo).hexdigest()}:{folder / 'photo.jpg'}",
            "filename": "photo.jpg",
            "mimeType": "image/jpeg",
            "inline": True,
        },
    ]
    # Images are left out of the plain text, and an image-only line leaves no blank hole.
    assert m.text == "Look:\n\n and "
    for att in m.attachments:
        _, digest, path = att["content"].split(":", 2)
        assert len(digest) == 64 and digest == digest.lower() and Path(path).is_absolute()


def test_other_images_are_dropped(tmp_path: Path) -> None:
    m = mail("x ![](https://evil.example/p.png) ![](assets/../secret.png) y", tmp_path)
    assert "<img" not in m.html and "evil" not in m.html
    assert m.attachments == []
    assert m.text == "x   y"


def test_missing_asset_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(AssetNotFound):
        mail("![](assets/gone.png)", tmp_path)


def test_images_need_a_recipient(tmp_path: Path) -> None:
    # The connector attaches files by reference only to a mail that names an address.
    with pytest.raises(MailError) as exc:
        build("", "S", "![](assets/p.png)", loader(tmp_path, {"p.png": png()}))
    assert exc.value.code == "no-recipient"
    assert build("", "S", "no images", loader(tmp_path, {})).to == []


def test_attachment_total_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("draft_studio.mailhtml.MAX_ATTACH_BYTES", 100)
    with pytest.raises(MailError) as exc:
        mail("![](assets/big.png)", tmp_path, {"big.png": png(40, 40, (1, 2, 3)) + b"\x00" * 200})
    assert exc.value.code == "too-large"
    assert MAX_ATTACH_BYTES == 25 * 1024 * 1024


def test_recipients() -> None:
    assert recipients(" a@b.c, d@e.f ;g@h.i,, ") == ["a@b.c", "d@e.f", "g@h.i"]
    assert recipients("") == []
    assert recipients("Ann Nowak <ann@x.org>, b@y.org") == ["ann@x.org", "b@y.org"]


def test_payload_drops_empty_fields(tmp_path: Path) -> None:
    m = build("", "", "Hi", loader(tmp_path, {}))
    assert m.payload() == {"body": "Hi", "htmlBody": "<p>Hi</p>"}


def test_plain_text_without_markup_is_unchanged(tmp_path: Path) -> None:
    """Property: text with none of the markdown syntax we render comes back byte for byte."""
    rng = random.Random(20261127)
    words = [
        "Hi",
        "Ann",
        "ąę",
        "Zürich",
        "2026",
        "a-b",
        "x.y",
        "(ok)",
        "we're",
        "50%",
        "@home",
        "Q?",
        "&",
        "<i>",
    ]
    for _ in range(300):
        lines = []
        for _ in range(rng.randint(1, 8)):
            n = rng.randint(0, 6)
            line = " ".join(rng.choice(words) for _ in range(n))
            lines.append(line)
        text = "\n".join(lines) + rng.choice(["", "\n", "\n\n"])
        out = build("a@b.c", "S", text, loader(tmp_path, {}))
        assert out.text == text, repr(text)


def test_blockquote_lines_become_one_quote(tmp_path: Path) -> None:
    m = mail("Intro\n> Hi **Ann**,\n>see below\n>\n> Best\nAfter", tmp_path)
    assert m.html == (
        "<p>Intro</p>\n<blockquote>\n<p>Hi <strong>Ann</strong>,<br>\nsee below</p>\n<p>Best</p>\n"
        "</blockquote>\n<p>After</p>"
    )
    assert m.text == "Intro\n> Hi Ann,\n> see below\n>\n> Best\nAfter"
    assert "&gt;" not in m.html


def test_blockquote_holds_headings_lists_and_code(tmp_path: Path) -> None:
    m = mail("> # Title\n> - one\n> - two\n>\n> ```\n> a < b\n> ```", tmp_path)
    assert m.html == (
        "<blockquote>\n<h1>Title</h1>\n<ul>\n<li>one</li>\n<li>two</li>\n</ul>\n"
        "<pre><code>a &lt; b</code></pre>\n</blockquote>"
    )
    assert m.text == "> Title\n> • one\n> • two\n>\n> a < b"


def test_nested_blockquote_does_not_leak_raw_markers(tmp_path: Path) -> None:
    m = mail("> outer\n>> inner\n> > deeper\n\nafter", tmp_path)
    assert m.html == (
        "<blockquote>\n<p>outer</p>\n<blockquote>\n<p>inner<br>\ndeeper</p>\n"
        "</blockquote>\n</blockquote>\n<p>after</p>"
    )
    assert m.text == "> outer\n> > inner\n> > deeper\n\nafter"


def test_escaped_marker_is_text_and_a_blank_line_ends_the_quote(tmp_path: Path) -> None:
    m = mail("\\> not a quote\n\n> one\n\n> two", tmp_path)
    assert m.html == (
        "<p>&gt; not a quote</p>\n<blockquote>\n<p>one</p>\n</blockquote>\n<blockquote>\n<p>two</p>\n</blockquote>"
    )
    assert m.text == "> not a quote\n\n> one\n\n> two"
