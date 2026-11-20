"""Every locale carries the same tooltip keys, and every icon-only button has one."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCALES = REPO / "locales"
WEB = REPO / "web" / "src"


def catalog(tag: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((LOCALES / f"{tag}.json").read_text())
    return data


def tips(tag: str) -> dict[str, str]:
    raw = catalog(tag).get("tips")
    assert isinstance(raw, dict), f"{tag}: no tips map"
    return {str(k): str(v) for k, v in raw.items()}


def icon_button_labels() -> set[str]:
    """data-label keys of icon-only buttons, from the pages and the TS that builds buttons."""
    keys: set[str] = set()
    for page in WEB.rglob("*.astro"):
        for tag in re.findall(r"<(?:button|a)\b[^>]*>", page.read_text()):
            if "icon-btn" in tag and (m := re.search(r'data-label="([^"]+)"', tag)):
                keys.add(m.group(1))
    for ts in WEB.rglob("*.ts"):
        if ts.name.endswith(".test.ts"):
            continue
        src = ts.read_text()
        keys.update(re.findall(r"iconButton\('[^']+', '([^']+)'", src))
        keys.update(re.findall(r"dataset\.label = '([^']+)'", src))
    return keys


def test_every_locale_has_the_same_tip_keys() -> None:
    want = set(tips("en"))
    assert want
    for f in sorted(LOCALES.glob("*.json")):
        got = set(tips(f.stem))
        assert got == want, f"{f.name}: missing {want - got}, extra {got - want}"
        assert all(v.strip() for v in tips(f.stem).values()), f"{f.name}: empty tip"


def test_every_icon_button_has_a_short_tip() -> None:
    buttons = icon_button_labels() - {"instruction", "kindMail", "kindText"}
    assert {"ask", "rewrite", "change", "acceptHunk", "rejectHunk", "delete", "restore"} <= buttons
    missing = buttons - set(tips("en"))
    assert not missing, f"icon buttons without a tip: {missing}"
    for key, text in tips("en").items():
        assert len(text.split()) <= 3, f"tip {key!r} is long: {text!r}"
