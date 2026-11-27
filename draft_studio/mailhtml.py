"""Draft markdown -> Gmail htmlBody, a plain-text body and inline image attachments.

Stdlib only, and only the subset a mail needs: paragraphs (a single newline is a
line break), # headings, > quotes, - / 1. lists, ``` fences, **bold**, *italic*, `code`,
[links](https://...) and ![](assets/<name>) images. Everything is HTML-escaped
first. Text that uses none of that syntax comes back unchanged as the plain
body (tests/test_mailhtml.py checks it as a property). The plain body keeps the
source line by line, so blank lines survive; images are left out of it.

Only images stored with the draft are attached: the connector removes every
other image from htmlBody, so a remote or odd src is dropped here, visibly.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path

from draft_studio.mail import Json, Mail, MailError
from draft_studio.store import ASSET_NAME_RE

# The connector's cap on all attachments of one mail.
MAX_ATTACH_BYTES = 25 * 1024 * 1024
Loader = Callable[[str], tuple[Path, bytes, str]]
# ![](src) -> the <img src> to emit, or None to drop the image (mail: cid refs, PDF: data URIs).
ImgSrc = Callable[[str], str | None]

_INLINE = re.compile(
    r"\\(?P<esc>[\\`*_{}\[\]()#+\-.!>])"
    r"|`(?P<code>[^`\n]+)`"
    r"|!\[(?P<alt>[^\]\n]*)\]\((?P<src>[^)\s]+)\)"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<href>[^)\s]+)\)"
    r"|\*\*(?=\S)(?P<b1>.+?)(?<=\S)\*\*"
    r"|__(?=\S)(?P<b2>.+?)(?<=\S)__(?!\w)"
    r"|(?<![\w*\\])\*(?=[^\s*])(?P<i1>.+?)(?<=[^\s*\\])\*(?![\w*])"
    r"|(?<![\w\\])_(?=[^\s_])(?P<i2>.+?)(?<=[^\s_\\])_(?!\w)"
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\d{1,9}[.)])\s+(.*)$")
_IMAGE_ONLY = re.compile(r"^\s*(?:!\[[^\]\n]*\]\([^)\s]+\)\s*)+$")
_QUOTE = re.compile(r"^>[ ]?(.*)$")
_FENCE = "```"
_SAFE_HREF = re.compile(r"^(?:https?://|mailto:)", re.IGNORECASE)


def recipients(to: str) -> list[str]:
    """The To field as plain addresses (the connector takes no display names): , or ; separated."""
    return [parseaddr(part)[1] for part in re.split(r"[,;]", to) if parseaddr(part)[1]]


@dataclass
class _Images:
    """Inline attachments in first-use order; one per stored file."""

    load: Loader
    attachments: list[Json] = field(default_factory=list)
    index: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def cid(self, src: str) -> str | None:
        name = src.removeprefix("assets/")
        if name == src or not ASSET_NAME_RE.match(name):
            return None
        if name not in self.index:
            path, data, mime = self.load(name)
            self.total += len(data)
            if self.total > MAX_ATTACH_BYTES:
                raise MailError("too-large")
            ref = f"sha256:{hashlib.sha256(data).hexdigest()}:{path.resolve().as_posix()}"
            self.index[name] = len(self.attachments)
            self.attachments.append({"content": ref, "filename": name, "mimeType": mime, "inline": True})
        return f"cid:att{self.index[name]}"


def _inline(src: str, images: ImgSrc) -> tuple[str, str]:
    """(html, plain text) of one line of inline markdown."""
    out_html: list[str] = []
    out_text: list[str] = []
    pos = 0
    for m in _INLINE.finditer(src):
        out_html.append(html.escape(src[pos : m.start()]))
        out_text.append(src[pos : m.start()])
        pos = m.end()
        h, t = _token(m, images)
        out_html.append(h)
        out_text.append(t)
    out_html.append(html.escape(src[pos:]))
    out_text.append(src[pos:])
    return "".join(out_html), "".join(out_text)


def _token(m: re.Match[str], images: ImgSrc) -> tuple[str, str]:
    g = m.groupdict()
    if g["esc"] is not None:
        return html.escape(g["esc"]), g["esc"]
    if g["code"] is not None:
        return f"<code>{html.escape(g['code'])}</code>", g["code"]
    if g["src"] is not None:
        cid = images(g["src"])
        return (f'<img src="{cid}" alt="{html.escape(g["alt"])}">' if cid else ""), ""
    if g["href"] is not None:
        inner_h, inner_t = _inline(g["label"], images)
        href = g["href"]
        text = inner_t if inner_t == href else f"{inner_t} ({href})"
        if not _SAFE_HREF.match(href):
            return inner_h, inner_t
        return f'<a href="{html.escape(href)}">{inner_h}</a>', text
    tag, inner = ("strong", g["b1"] or g["b2"]) if g["b1"] or g["b2"] else ("em", g["i1"] or g["i2"])
    inner_h, inner_t = _inline(inner, images)
    return f"<{tag}>{inner_h}</{tag}>", inner_t


def _blocks(lines: list[str], images: ImgSrc) -> tuple[list[str], list[str]]:
    """(html blocks, plain lines). The plain lines follow the source one by one."""
    blocks: list[str] = []
    plain: list[str] = []
    para: list[str] = []
    items: list[str] = []
    kind = ""

    def close() -> None:
        nonlocal kind
        if para:
            blocks.append("<p>" + "<br>\n".join(para) + "</p>")
            para.clear()
        if items:
            blocks.append(f"<{kind}>\n" + "\n".join(f"<li>{i}</li>" for i in items) + f"\n</{kind}>")
            items.clear()
        kind = ""

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith(_FENCE):
            close()
            code: list[str] = []
            while i < len(lines) and not lines[i].startswith(_FENCE):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            plain.extend(code)
            continue
        if not line.strip():
            close()
            plain.append(line)
            continue
        if _QUOTE.match(line):
            close()
            quoted = [_QUOTE.sub(r"\1", line)]
            while i < len(lines) and _QUOTE.match(lines[i]):
                quoted.append(_QUOTE.sub(r"\1", lines[i]))
                i += 1
            inner, inner_plain = _blocks(quoted, images)
            blocks.append("<blockquote>\n" + "\n".join(inner) + "\n</blockquote>")
            plain.extend(f"> {t}" if t else ">" for t in inner_plain)
            continue
        if heading := _HEADING.match(line):
            close()
            h, t = _inline(heading.group(2), images)
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{h}</h{level}>")
            plain.append(t)
            continue
        marker, body, want = "", "", ""
        if bullet := _BULLET.match(line):
            marker, body, want = "•", bullet.group(1), "ul"
        elif number := _NUMBER.match(line):
            marker, body, want = number.group(1), number.group(2), "ol"
        if marker:
            if kind != want:
                close()
                kind = want
            h, t = _inline(body, images)
            items.append(h)
            plain.append(f"{marker} {t}")
            continue
        if items:
            close()
        h, t = _inline(line, images)
        para.append(h)
        if not _IMAGE_ONLY.match(line):
            plain.append(t)
    close()
    return blocks, plain


def html_body(markdown: str, images: ImgSrc) -> str:
    """The HTML blocks of `markdown`, images resolved by `images` (the PDF export's entry)."""
    return "\n".join(_blocks(markdown.split("\n"), images)[0])


def build(to: str, subject: str, markdown: str, load: Loader) -> Mail:
    """The mail for a draft; `load(name)` gives (path, bytes, mime) of a stored image."""
    images = _Images(load)
    blocks, plain = _blocks(markdown.split("\n"), images.cid)
    # Seen live: the connector attaches files by reference only to a mail that names an address.
    if images.attachments and not recipients(to):
        raise MailError("no-recipient")
    return Mail(
        to=recipients(to),
        subject=subject,
        text="\n".join(plain),
        html="\n".join(blocks),
        attachments=images.attachments,
    )
