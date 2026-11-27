"""Claude's screenshot requests: parsing, the URL allowlist, alt text, marker swapping."""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.shotreq import (
    ShotError,
    allowed_url,
    clean_alt,
    marker,
    markers,
    parse_shot,
    parse_shots,
    substitute,
    unknown,
    urls_in,
)

RAW: dict[str, Any] = {
    "key": "panel-1",
    "url": "https://panel.example/",
    "steps": [{"kind": "click", "text": " Twoje usługi "}, {"kind": "wait", "text": "Lista"}],
    "highlight": "DNS",
    "alt": "Panel [x](y)\nnext",
}


def test_parse_shot_and_its_two_shapes() -> None:
    shot = parse_shot(RAW)
    assert shot.steps == (("click", ("Twoje usługi", "", "", 0)), ("wait", ("Lista", "", "", 0)))
    assert shot.alt == "Panel x y next"
    assert shot.spec() == {
        "url": "https://panel.example/",
        "name": "panel-1",
        "steps": [{"click": "Twoje usługi"}, {"wait": "Lista"}],
        "highlight": "DNS",
    }
    assert parse_shot(shot.to_json()) == shot
    assert parse_shot({**RAW, "highlight": ""}).spec()["highlight"] is None
    assert parse_shot({**RAW, "key": "Bad Key"}, key="panel-1").key == "panel-1"  # a fix keeps the key


@pytest.mark.parametrize(
    "raw",
    [
        "nope",
        {**RAW, "key": "Bad Key"},
        {**RAW, "key": 5},
        {**RAW, "steps": "x"},
        {**RAW, "steps": ["x"]},
        {**RAW, "steps": [{"kind": "type", "text": "a"}]},
        {**RAW, "url": None},
        {**RAW, "fresh": "yes"},
        {**RAW, "highlight": 5},
        {**RAW, "highlight": {"role": "link"}},
        {**RAW, "highlight": {"text": "a", "nth": True}},
        {**RAW, "highlight": {"text": "a", "nth": "1"}},
        {**RAW, "steps": [{"kind": "click", "text": "", "role": ""}]},
    ],
)
def test_bad_shots(raw: Any) -> None:
    with pytest.raises(ShotError) as err:
        parse_shot(raw)
    assert err.value.code == "bad-spec"


def test_parse_shots_drops_bad_and_repeated_keys() -> None:
    other = {**RAW, "key": "other"}
    shots, bad = parse_shots([RAW, {**RAW, "key": "x y"}, "junk", other, {**other, "alt": "again"}])
    assert list(shots) == ["panel-1"]
    assert bad == ["x y", "?", "other"]


def test_urls_must_be_written_by_kuhy() -> None:
    kuhy = "Otworzyć https://panel.cyberfolks.pl/. Potem „https://x.example/a?b=1”, oraz (http://y.example)."
    assert urls_in(kuhy) == {"https://panel.cyberfolks.pl", "https://x.example/a?b=1", "http://y.example"}
    assert allowed_url("https://panel.cyberfolks.pl", kuhy)
    assert allowed_url("https://panel.cyberfolks.pl/", kuhy)
    assert not allowed_url("https://panel.cyberfolks.pl/invented", kuhy)
    assert not allowed_url("https://evil.example/", kuhy)


def test_clean_alt_is_one_short_line() -> None:
    assert clean_alt("a ![b](c)\n<d> `e` *f*_g") == "a b c d e f g"
    assert len(clean_alt("x" * 500)) == 200


def test_markers_in_order_once() -> None:
    text = f"a\n{marker('b')}\nc [[[ IMG: a ]]]\n{marker('b')}"
    assert markers(text) == ["b", "a"]
    assert unknown(["b", "a"], {"a": parse_shot(RAW)}) == ["b"]


def test_substitute_swaps_and_drops_lines() -> None:
    text = f"Step 1\n\n{marker('a')}\n\nStep 2\n\n{marker('b')}\n\nStep 3"
    out, dropped = substitute(text, {"a": "![A](assets/a.png)"})
    assert out == "Step 1\n\n![A](assets/a.png)\n\nStep 2\n\nStep 3"
    assert dropped == ["b"]
    assert substitute(text, {"a": "A", "b": "B"}) == ("Step 1\n\nA\n\nStep 2\n\nB\n\nStep 3", [])
    assert substitute("no markers", {}) == ("no markers", [])


def test_substitute_cuts_inline_and_malformed_markers() -> None:
    assert substitute(f"see {marker('a')} here", {}) == ("see  here", ["a"])
    out, dropped = substitute("x\n\n[[[IMG:broken\n\ny", {})
    assert (out, dropped) == ("x\n\ny", ["?"])
    out, dropped = substitute(f"x\n\n{marker('a')} [[[img:b", {"a": "A"})
    assert (out, dropped) == ("x", ["?"])


def test_sentinels_normalise_into_exact_aims() -> None:
    """Structured output fills every field: "" and 0 mean unset, a text wins over a role."""

    def aim(text: str = "", role: str = "", within: str = "", nth: int = 0) -> dict[str, object]:
        return {"text": text, "role": role, "within": within, "nth": nth}

    steps = [
        {"kind": "click", **aim("ZALOGUJ JAKO", within="GABINET")},
        {"kind": "click", **aim(role="button", within="kaliszpsycholog.pl", nth=-1)},
        {"kind": "goto", **aim("https://panel.example/x", role="button", nth=2)},
        {"kind": "click", **aim("Odnów", role="button")},
    ]
    shot = parse_shot({**RAW, "steps": steps, "highlight": aim(), "walk": " renew ", "fresh": True})
    assert shot.spec()["steps"] == [
        {"click": {"text": "ZALOGUJ JAKO", "within": "GABINET"}},
        {"click": {"role": "button", "within": "kaliszpsycholog.pl", "nth": -1}},
        {"goto": "https://panel.example/x"},
        {"click": "Odnów"},
    ]
    assert shot.spec()["highlight"] is None and (shot.walk, shot.fresh) == ("renew", True)
    assert shot.urls() == ("https://panel.example/", "https://panel.example/x")
    assert parse_shot(shot.to_json()) == shot
    assert parse_shot({**RAW, "highlight": aim("Domeny", nth=1)}).spec()["highlight"] == {
        "text": "Domeny",
        "nth": 1,
    }
