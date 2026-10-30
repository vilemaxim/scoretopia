"""Tests for OCR-tolerant matching of player names against a known roster."""

from __future__ import annotations

from scoretopia.screenshot.name_matching import match_known_name, ocr_name_key


def test_ocr_name_key_drops_dots_and_folds_one_like_glyphs() -> None:
    assert ocr_name_key("a.b.cdef") == ocr_name_key("abcdef")
    assert ocr_name_key("player1") == ocr_name_key("playerl")
    assert ocr_name_key("playerI") == ocr_name_key("player|")
    assert ocr_name_key("Name") == ocr_name_key("name")


def test_ocr_name_key_folds_zero_and_letter_o() -> None:
    assert ocr_name_key("mouse01") == ocr_name_key("mouseO1")


def test_ocr_name_key_keeps_other_differences() -> None:
    assert ocr_name_key("alpha") != ocr_name_key("alpho")
    assert ocr_name_key("ab cd") != ocr_name_key("abcd")


def test_match_known_name_returns_canonical_spelling() -> None:
    roster = ["al.pha.one", "bravo2"]
    assert match_known_name("alphaone", roster) == "al.pha.one"
    assert match_known_name("bravo2", roster) == "bravo2"


def test_match_known_name_folds_l_and_one() -> None:
    assert match_known_name("charliel", ["charlie1"]) == "charlie1"


def test_match_known_name_none_when_unknown() -> None:
    assert match_known_name("delta", ["echo", "foxtrot"]) is None


def test_match_known_name_none_when_ambiguous() -> None:
    assert match_known_name("golf", ["g.olf", "go.lf"]) is None


def test_match_known_name_empty_inputs() -> None:
    assert match_known_name("", ["echo"]) is None
    assert match_known_name("echo", []) is None
