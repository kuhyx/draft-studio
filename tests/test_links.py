"""Image links in markdown: which `assets/` targets the draft does not hold, which images Claude made up."""

from __future__ import annotations

from draft_studio.links import invented_images, missing_assets

STORED = {"panel-1a2b3c4d5e6f.png", "logo-0a1b2c3d4e5f.jpg"}
# The incident: draft studio's own Claude linked screenshots nobody had uploaded.
INCIDENT = "Kliknij Panel:\n\n![Panel cyber_Folks](assets/TODO-cyberfolks-01-panel-logowania.png)\n"


def test_incident_link_is_missing() -> None:
    assert missing_assets(INCIDENT, STORED) == ["TODO-cyberfolks-01-panel-logowania.png"]


def test_stored_images_are_not_missing() -> None:
    text = "![](assets/panel-1a2b3c4d5e6f.png)\n![logo](assets/logo-0a1b2c3d4e5f.jpg)"
    assert missing_assets(text, STORED) == []
    assert invented_images(text, STORED) == []


def test_link_forms_all_count() -> None:
    text = (
        "[see](assets/a.png) ![b](./assets/b.png) ![c](<assets/c.png>) "
        '![d](assets/d.png "title") ![e](assets/../meta.json) ![f](assets/)'
    )
    assert missing_assets(text, STORED) == ["a.png", "b.png", "c.png", "d.png", "../meta.json", ""]


def test_repeats_reported_once_in_order() -> None:
    text = "![](assets/z.png) ![](assets/y.png) ![](assets/z.png)"
    assert missing_assets(text, STORED) == ["z.png", "y.png"]


def test_plain_text_and_other_links_are_not_asset_links() -> None:
    text = "assets/x.png is a path. [site](https://example.com/assets/x.png) ![r](https://e.com/r.png)"
    assert missing_assets(text, STORED) == []


def test_invented_images_also_flags_images_outside_assets() -> None:
    text = (
        "![shot](screenshot.png) ![r](https://e.com/r.png) [doc](assets/doc.png) "
        "![ok](assets/panel-1a2b3c4d5e6f.png) ![shot](screenshot.png)"
    )
    assert invented_images(text, STORED) == ["doc.png", "screenshot.png", "https://e.com/r.png"]
