"""Image links in draft markdown, checked against the files the draft really holds.

kuhy (2026-11-27): "fake images are worse than no image at all". Draft studio's
own Claude once linked seven `assets/TODO-...png` screenshots nobody had
uploaded. Two gates use this module: api.py drops a Claude answer with any such
link before the client sees it, and approval.py refuses to approve (so Gmail
and PDF refuse to export) while the text links a missing asset. kuhy's own
saves are never refused: his edits always win.
"""

from __future__ import annotations

import re
from collections.abc import Collection

# Any markdown link or image target under assets/: ](assets/x), ](./assets/x), ](<assets/x>).
_ASSET_TARGET = re.compile(r"\]\(\s*<?(?:\./)?assets/([^)\s>]*)")
# Any markdown image source.
_IMAGE_SRC = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)\s>]*)")
_ASSET_PREFIX = re.compile(r"^(?:\./)?assets/")


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def missing_assets(text: str, stored: Collection[str]) -> list[str]:
    """Names linked as assets/<name> that are not stored, once each, in text order.

    A name the store could never hold (bad charset, `..`) is never stored, so it counts as missing.
    """
    return _unique([name for name in _ASSET_TARGET.findall(text) if name not in stored])


def invented_images(text: str, stored: Collection[str]) -> list[str]:
    """Everything Claude may not write: missing assets plus any image whose source is not an asset."""
    foreign = [src for src in _IMAGE_SRC.findall(text) if not _ASSET_PREFIX.match(src)]
    return _unique(missing_assets(text, stored) + foreign)
