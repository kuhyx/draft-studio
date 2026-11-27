"""Pending proposals (draft_studio/proposals.py): positions through text changes, landing, resolving."""

from __future__ import annotations

from pathlib import Path

import pytest

from draft_studio import proposals as pr


def prop(pid: int, start: int, end: int, replacement: str = "x", rev: int = 1) -> dict[str, object]:
    return {"id": pid, "from": start, "to": end, "replacement": replacement, "rev": rev}


def data(*items: dict[str, object], seq: int = 9) -> dict[str, object]:
    return {"seq": seq, "list": list(items), "gone": {}}


def test_file_round_trip_and_default(tmp_path: Path) -> None:
    assert pr.read(tmp_path) == pr.empty()
    saved = data(prop(1, 0, 3))
    pr.write(tmp_path, saved)
    assert pr.read(tmp_path) == saved
    assert pr.view(saved) == {"proposals": saved["list"], "gone": {}}


def test_utf16_positions_count_astral_characters_twice() -> None:
    text = "a😀b"
    assert pr.width(text) == 4
    assert pr.cut(text, 1, 3) == "😀"
    assert pr.cut(text, 3, 4) == "b"
    assert pr.cut(text, 0, 99) == text
    assert pr.replace_span(text, 1, 3, "-") == "a-b"


def test_changes_are_the_regions_that_differ() -> None:
    assert pr.changes("same", "same") == []
    assert pr.changes("abc def ghi", "abc XY ghi") == [(4, 7, -1)]
    assert pr.changes("a b c", "a X c Y") == [(2, 3, 0), (5, 5, 2)]


def test_changes_of_huge_texts_are_one_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "DIFF_LIMIT", 4)
    assert pr.changes("a12345b", "a67890b") == [(1, 6, 0)]


def test_shift_moves_untouched_spans_and_refuses_touched_ones() -> None:
    regions = pr.changes("0123456789", "01AB23456789")  # two units added at 2
    assert pr.shift(regions, 4, 6) == (6, 8)
    assert pr.shift(regions, 0, 1) == (0, 1)
    assert pr.shift(regions, 0, 2) is None  # a change at the edge touches
    assert pr.shift(regions, 1, 5) is None


def test_remap_drops_touched_proposals_and_records_why() -> None:
    before = "Warm and short greetings. Second line."
    after = "WaXrm and short greetings. Second line is longer."
    out = pr.remap(data(prop(1, 0, 14), prop(2, 26, 31)), before, after)
    assert [p["id"] for p in out["list"]] == [2] and out["gone"] == {"1": "stale"}
    assert (out["list"][0]["from"], out["list"][0]["to"]) == (27, 32)
    assert pr.remap(data(prop(1, 0, 3)), "same", "same") == data(prop(1, 0, 3))


def test_remap_shifts_a_proposal_after_an_insert_before_it() -> None:
    out = pr.remap(data(prop(1, 4, 9)), "one two three", "zero one two three")
    assert (out["list"][0]["from"], out["list"][0]["to"]) == (9, 14)


def test_gone_keeps_the_last_fifty() -> None:
    many = {"seq": 1, "list": [], "gone": {str(i): "stale" for i in range(60)}}
    out = pr.rejected({**many, "list": [prop(99, 0, 1)]}, prop(99, 0, 1), None, "t")
    assert len(out["gone"]) == pr.KEEP and out["gone"]["99"] == "rejected"


def test_add_lands_a_proposal_with_a_fresh_id() -> None:
    out, outcome, pid = pr.add(data(prop(1, 20, 30), seq=2), 0, 5, "new", revises=[], span="old..")
    assert (outcome, pid, out["seq"]) == ("ok", 2, 3)
    assert [p["id"] for p in out["list"]] == [2, 1]  # sorted by position


def test_add_keeps_the_missing_shots_on_the_proposal_only_when_there_are_any() -> None:
    out, _, _ = pr.add(data(prop(1, 20, 30), seq=2), 0, 5, "new", revises=[], span="old..", missing=["b"])
    assert out["list"][0]["missing"] == ["b"] and "missing" not in out["list"][1]
    out, _, _ = pr.add(data(), 0, 5, "new", revises=[], span="old..")
    assert "missing" not in out["list"][0]


def test_a_revision_replaces_the_missing_shots_of_the_one_it_revised() -> None:
    start, _, _ = pr.add(data(), 0, 5, "new", revises=[], span="old..", missing=["b"])
    out, _, _ = pr.add(start, 0, 5, "newer", revises=[9], span="old..")
    assert "missing" not in out["list"][0]


def test_add_refuses_an_overlap_and_a_revision_of_a_vanished_proposal() -> None:
    start = data(prop(1, 0, 10))
    assert pr.add(start, 5, 15, "x", revises=[], span="span") == (start, "overlap", None)
    assert pr.add(start, 0, 10, "x", revises=[7], span="span") == (start, "gone", None)


def test_add_revises_in_place_keeping_the_id_and_bumping_rev() -> None:
    start = data(prop(1, 0, 4, rev=3), prop(2, 6, 9))
    out, outcome, pid = pr.add(start, 0, 9, "union", revises=[1, 2], span="0123456789"[:9])
    assert (outcome, pid, out["seq"]) == ("ok", 1, 9)
    assert out["list"] == [prop(1, 0, 9, "union", rev=4)]


def test_add_an_answer_equal_to_the_text_retires_what_it_revised() -> None:
    out, outcome, pid = pr.add(data(prop(1, 0, 4)), 0, 4, "same", revises=[1], span="same")
    assert (outcome, pid, out["list"]) == ("ok", None, [])


def test_find_checks_the_rev() -> None:
    start = data(prop(1, 0, 4, rev=2))
    assert pr.find(start, 1, None)["id"] == 1 and pr.find(start, 1, 2)["id"] == 1
    with pytest.raises(pr.StaleProposal):
        pr.find(start, 1, 1)
    with pytest.raises(pr.NoProposal):
        pr.find(start, 5, None)


def test_accepted_whole_removes_it_and_moves_the_later_ones() -> None:
    start = data(prop(1, 0, 4, "longer text"), prop(2, 10, 12), prop(0, 0, 0))
    start["list"] = [start["list"][2], *start["list"][:2]]
    out = pr.accepted(start, start["list"][1], "longer text")
    assert [(p["id"], p["from"], p["to"]) for p in out["list"]] == [(0, 0, 0), (2, 17, 19)]
    assert out["gone"] == {"1": "accepted"}


def test_accepted_partly_keeps_it_for_the_rest() -> None:
    start = data(prop(1, 0, 4, "a b c"))
    out = pr.accepted(start, start["list"][0], "a b4")
    assert out["list"] == [prop(1, 0, 4, "a b c", rev=2)] and out["gone"] == {}


def test_rejected_whole_or_down_to_the_hunks_kept() -> None:
    start = data(prop(1, 0, 4, "a b c"), prop(2, 6, 9))
    whole = pr.rejected(start, start["list"][0], None, "xxxx")
    assert [p["id"] for p in whole["list"]] == [2] and whole["gone"] == {"1": "rejected"}
    partly = pr.rejected(start, start["list"][0], "a b4", "xxxx")
    assert partly["list"][0] == prop(1, 0, 4, "a b4", rev=2) and partly["list"][1] == start["list"][1]
    same = pr.rejected(start, start["list"][0], "xxxx", "xxxx")  # nothing of it left to propose
    assert [p["id"] for p in same["list"]] == [2]


@pytest.mark.parametrize(
    ("ranges", "start", "end", "size", "want"),
    [
        ([], 2, 5, 3, [[2, 5]]),
        ([[0, 2]], 2, 5, 3, [[0, 5]]),  # touching ranges merge
        ([[0, 4]], 2, 5, 1, [[0, 3]]),  # the replaced tail goes, the new text is Claude's
        ([[3, 9]], 2, 5, 0, [[2, 6]]),  # an empty replacement only cuts
        ([[0, 10]], 2, 5, 3, [[0, 10]]),
        ([[7, 9]], 2, 5, 1, [[2, 3], [5, 7]]),
    ],
)
def test_claude_ranges_after_an_accept(
    ranges: list[list[int]], start: int, end: int, size: int, want: list[list[int]]
) -> None:
    assert pr.claude_after(ranges, start, end, size) == want
