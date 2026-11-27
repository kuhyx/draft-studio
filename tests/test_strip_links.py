"""strip_invented: Claude's made-up image links go, the rest of its text and kuhy's own links stay."""

from __future__ import annotations

import pytest

from draft_studio.links import LinkError, invented_images, strip_invented

STORED = {"panel-1a2b3c4d5e6f.png"}
FAKE = "![](assets/TODO-cyberfolks-01-panel.png)"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # A line holding only the link goes, with one blank separator.
        (f"Alpha 2 beta\n\n{FAKE}", "Alpha 2 beta"),
        (f"Alpha\n\n{FAKE}\n\nOmega", "Alpha\n\nOmega"),
        (f"Alpha\n{FAKE}\nOmega", "Alpha\nOmega"),
        (f"Alpha\n\n{FAKE}\nOmega", "Alpha\n\nOmega"),
        (f"{FAKE}\n\nOmega", "Omega"),
        (f"Alpha\n\n{FAKE}\n", "Alpha\n"),
        (f"Alpha\n\n{FAKE}\n\n![x](shot.png)\n\nOmega", "Alpha\n\nOmega"),
        (f"  {FAKE}  ", ""),
        (f"- {FAKE}\n> {FAKE}\n1. {FAKE}\nend", "end"),
        # Inline: the link goes, the spaces close up, the words stay.
        (f"Look {FAKE} here", "Look here"),
        (f"Look {FAKE}.", "Look."),
        (f"Look{FAKE}here", "Look here"),
        (f"({FAKE} x)", "(x)"),
        (f"  {FAKE} indented", "  indented"),
        (f"Look {FAKE}", "Look"),
        (f"Look {FAKE} {FAKE} twice", "Look twice"),
        # A plain link to a missing asset keeps its words.
        ("Open [the panel](assets/gone.png) now", "Open the panel now"),
        ("Open [](assets/gone.png) now", "Open now"),
        # Every link form counts.
        ('A ![t](<./assets/x.png> "title") B', "A B"),
        ("A ![t](https://e.com/r.png 'title') B", "A B"),
        # Nothing to strip: byte-for-byte the same.
        ("Hi\n\n![](assets/panel-1a2b3c4d5e6f.png)\n", "Hi\n\n![](assets/panel-1a2b3c4d5e6f.png)\n"),
        ("[site](https://example.com) and [x](notes.md)", "[site](https://example.com) and [x](notes.md)"),
        ("", ""),
    ],
)
def test_strips_only_the_invented_links(text: str, want: str) -> None:
    out, stripped = strip_invented(text, STORED)
    assert out == want
    assert invented_images(out, STORED) == []
    assert bool(stripped) == (out != text)


def test_reports_each_stripped_link_in_order() -> None:
    text = f"A {FAKE} B ![s](shot.png) [d](assets/doc.png) ![ok](assets/panel-1a2b3c4d5e6f.png)"
    out, stripped = strip_invented(text, STORED)
    assert stripped == [FAKE, "![s](shot.png)", "[d](assets/doc.png)"]
    assert out == "A B d ![ok](assets/panel-1a2b3c4d5e6f.png)"


def test_kuhys_own_links_survive_even_remote_or_missing() -> None:
    kuhy = "Logo ![me](https://kuhy.pl/me.png) and ![](assets/typed-by-kuhy.png) [cv](./assets/cv.pdf)"
    claude = (
        "Logo ![Me, smiling](https://kuhy.pl/me.png)\n![](./assets/typed-by-kuhy.png)\n"
        "[CV](<assets/cv.pdf>)\n![](https://kuhy.pl/other.png)"
    )
    out, stripped = strip_invented(claude, STORED, kuhy)
    assert stripped == ["![](https://kuhy.pl/other.png)"]
    assert out == claude.rsplit("\n", 1)[0]


def test_an_unparsable_link_fails_closed() -> None:
    # The detector sees an image, the stripper cannot cut it cleanly: never pass it on.
    with pytest.raises(LinkError, match=r"x\.png"):
        strip_invented("A ![a](x.png unclosed", STORED)
