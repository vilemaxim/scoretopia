"""Tests for OCR tribe name resolution (Task 002)."""

import pytest
from scoretopia.screenshot.tribes import CANONICAL_TRIBES, resolve_ocr_tribe


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Xf yrion", "Elyrion"),
        ("xf yrion", "Elyrion"),
        ("XF YRION", "Elyrion"),
    ],
)
def test_resolve_ocr_tribe_fuzzy_matches_elyrion(raw: str, expected: str) -> None:
    assert resolve_ocr_tribe(raw) == expected


@pytest.mark.parametrize(
    "name",
    ["Imperius", "Vengir", "Xin-xi", "Ai-Mo"],
)
def test_resolve_ocr_tribe_exact_names_pass_through(name: str) -> None:
    assert resolve_ocr_tribe(name) == name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("imperius", "Imperius"),
        ("IMPERIUS", "Imperius"),
        ("xin-xi", "Xin-xi"),
        ("XIN-XI", "Xin-xi"),
        ("ai-mo", "Ai-Mo"),
        ("AI-MO", "Ai-Mo"),
    ],
)
def test_resolve_ocr_tribe_case_insensitive_exact_match(
    raw: str, expected: str
) -> None:
    assert resolve_ocr_tribe(raw) == expected


def test_resolve_ocr_tribe_unmatched_returns_raw_string() -> None:
    assert resolve_ocr_tribe("randomtext") == "randomtext"


def test_canonical_tribes_lists_all_sixteen_official_tribes() -> None:
    expected = {
        "Xin-xi",
        "Imperius",
        "Bardur",
        "Oumaji",
        "Kickoo",
        "Hoodrick",
        "Luxidoor",
        "Vengir",
        "Zebasi",
        "Ai-Mo",
        "Quetzali",
        "Yadakk",
        "Aquarion",
        "Elyrion",
        "Polaris",
        "Cymanti",
    }
    assert set(CANONICAL_TRIBES) == expected
    assert len(CANONICAL_TRIBES) == 16
