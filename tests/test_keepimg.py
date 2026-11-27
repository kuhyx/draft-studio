"""A proposal never loses an image the span held (kuhy 2026-11-27: the old shots vanished with the new ones)."""

from __future__ import annotations

import pytest

from draft_studio.keepimg import restore
from draft_studio.shotreq import marker, substitute

OLD_A = "![Twoje usługi](assets/krok3.png)"
OLD_B = "![Domeny](assets/krok4.png)"
NEW_A = "![nowy](assets/new-1.png)"
SPAN = f"1. Zaloguj się\n\n{OLD_A}\n\n2. Otwórz domeny\n\n{OLD_B}"


def final(answer: str, done: dict[str, str], span: str = SPAN, instruction: str = "popraw") -> str:
    """The text a release would deliver (markers swapped, restored images in)."""
    text, extra = restore(answer, [span], instruction, done)
    return substitute(text, {**done, **extra})[0]


def test_old_images_stay_when_every_new_shot_is_missing() -> None:
    answer = f"1. Zaloguj się do panelu\n\n{marker('s1')}\n\n2. Otwórz Domeny\n\n{marker('s2')}"
    assert final(answer, {}) == f"1. Zaloguj się do panelu\n\n{OLD_A}\n\n2. Otwórz Domeny\n\n{OLD_B}"


def test_an_old_image_goes_when_its_new_shot_exists() -> None:
    answer = f"1. Zaloguj się do panelu\n\n{marker('s1')}\n\n2. Otwórz Domeny\n\n{marker('s2')}"
    out = final(answer, {"s1": NEW_A})
    assert out == f"1. Zaloguj się do panelu\n\n{NEW_A}\n\n2. Otwórz Domeny\n\n{OLD_B}"


def test_both_replaced_leaves_no_old_image() -> None:
    answer = f"1. A\n\n{marker('s1')}\n\n2. B\n\n{marker('s2')}"
    out = final(answer, {"s1": NEW_A, "s2": "![x](assets/new-2.png)"})
    assert "krok" not in out and "new-1" in out and "new-2" in out


def test_an_old_image_without_any_marker_is_put_back_after_its_hunk() -> None:
    answer = "1. Zaloguj się do panelu\n\n2. Otwórz Domeny"
    out = final(answer, {})
    assert OLD_A in out and OLD_B in out
    assert "\n\n\n" not in out
    assert out.startswith("1. Zaloguj się do panelu")


def test_an_image_pure_deletion_is_put_back_in_place() -> None:
    span = f"Intro\n\n{OLD_A}\n\nOutro"
    out = final("Intro\n\nOutro", {}, span)
    assert out == f"Intro\n\n{OLD_A}\n\nOutro"


def test_an_image_the_answer_kept_is_not_doubled() -> None:
    answer = f"1. Zaloguj się\n\n{OLD_A}\n\n2. Otwórz domeny\n\n{OLD_B}"
    assert final(answer, {}) == answer


def test_an_image_moved_elsewhere_counts_as_kept() -> None:
    answer = f"{OLD_B}\n\n{OLD_A}\n\n1. Zaloguj się"
    assert final(answer, {}) == answer


@pytest.mark.parametrize(
    "instruction",
    [
        "usuń zrzuty ekranu",
        "Remove the screenshots",
        "delete the second image",
        "skasuj zdjęcie",
        "usun obrazek",
    ],
)
def test_the_instruction_may_ask_for_removal(instruction: str) -> None:
    answer = "1. Zaloguj się\n\n2. Otwórz domeny"
    assert final(answer, {}, instruction=instruction) == answer


@pytest.mark.parametrize("instruction", ["remove the filler words", "usuń zbędne słowa", "make it shorter"])
def test_a_removal_of_something_else_does_not_license_it(instruction: str) -> None:
    answer = "1. Zaloguj się\n\n2. Otwórz domeny"
    assert OLD_A in final(answer, {}, instruction=instruction)


def test_alt_text_is_not_the_identity_only_the_target() -> None:
    answer = f"1. Zaloguj się\n\n![inny opis](assets/krok3.png)\n\n2. Otwórz domeny\n\n{OLD_B}"
    assert final(answer, {}) == answer


def test_images_of_a_previous_replacement_count_too() -> None:
    previous = "Step\n\n![p](assets/prev.png)"
    text, extra = restore("Step rewritten", [SPAN, previous], "x", {})
    out = substitute(text, extra)[0]
    assert "assets/prev.png" in out and "assets/krok3.png" in out and "assets/krok4.png" in out


def test_one_old_image_is_restored_once_across_baselines() -> None:
    text, extra = restore("Rewritten", [SPAN, SPAN], "x", {})
    assert substitute(text, extra)[0].count("krok3.png") == 1


def test_no_old_images_changes_nothing() -> None:
    answer = f"Hello\n\n{marker('s1')}"
    assert restore(answer, ["Hello"], "x", {}) == (answer, {})


def test_inline_images_in_a_text_line_are_not_tracked() -> None:
    assert restore("short", ["see ![i](assets/a.png) here"], "x", {}) == ("short", {})


def test_a_leftover_old_image_with_a_blank_line_neighbour_gets_no_extra_blank() -> None:
    span = f"Intro\n\n{OLD_A}\n"
    out = final("Intro\n", {}, span)
    assert OLD_A in out and "\n\n\n" not in out
