"""Tests for fuzzy player-name matching (Task 022)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scoretopia.screenshot.parsers import _names_match, _parse_win_ratio

PARSERS_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "scoretopia"
    / "screenshot"
    / "parsers.py"
)


def _player_names_match(actual: str, expected: str, *, screenshot_type: str) -> bool:
    from scoretopia.screenshot.name_matching import player_names_match

    return player_names_match(actual, expected, screenshot_type=screenshot_type)


# --- player_names_match: game_end ---


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("Alice", "Alice"),
        ("alice", "ALICE"),
        ("  Bob  ", "Bob"),
    ],
)
def test_player_names_match_game_end_exact(actual: str, expected: str) -> None:
    assert _player_names_match(actual, expected, screenshot_type="game_end") is True


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("VeryLongPlay", "VeryLongPlayerName"),
        ("VeryLongPlayerName", "VeryLongPlay"),
    ],
)
def test_player_names_match_game_end_prefix_truncation_both_directions(
    actual: str,
    expected: str,
) -> None:
    assert _player_names_match(actual, expected, screenshot_type="game_end") is True


def test_player_names_match_game_end_ocr_typo_after_normalization() -> None:
    assert (
        _player_names_match("DiremousO1", "Diremouse01", screenshot_type="game_end")
        is True
    )


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("Alice", "Bob"),
        ("Al", "Alice"),
        ("XY", "XYZ"),
        ("Alice", "Alicia"),
    ],
)
def test_player_names_match_game_end_clear_non_matches(
    actual: str,
    expected: str,
) -> None:
    assert _player_names_match(actual, expected, screenshot_type="game_end") is False


def test_player_names_match_game_end_empty_or_none_is_false() -> None:
    assert _player_names_match("", "Alice", screenshot_type="game_end") is False
    assert _player_names_match("Alice", "", screenshot_type="game_end") is False


# --- player_names_match: game_basics (exact only) ---


def test_player_names_match_game_basics_exact_match() -> None:
    assert _player_names_match("Alice", "alice", screenshot_type="game_basics") is True


def test_player_names_match_game_basics_rejects_truncation() -> None:
    assert (
        _player_names_match(
            "VeryLongPlay",
            "VeryLongPlayerName",
            screenshot_type="game_basics",
        )
        is False
    )


def test_player_names_match_game_basics_rejects_fuzzy_typo() -> None:
    assert (
        _player_names_match(
            "DiremousO1",
            "Diremouse01",
            screenshot_type="game_basics",
        )
        is False
    )


# --- game_end winner detection via _names_match ---


def test_names_match_accepts_truncated_winner_for_is_winner() -> None:
    assert _names_match("VeryLongPlay", "VeryLongPlayerName") is True


def test_names_match_accepts_ocr_typo_for_winner() -> None:
    assert _names_match("DiremousO1", "Diremouse01") is True


def test_names_match_rejects_unrelated_players() -> None:
    assert _names_match("Alice", "Bob") is False


# --- _parse_win_ratio: no hardcoded sample names ---


def test_parse_win_ratio_uses_you_token_not_hardcoded_names() -> None:
    lines = [
        "Bob (friend)",
        "Win ratio",
        "Alice",
        "16",
        "you",
        "22",
    ]
    ratio = _parse_win_ratio(lines, friend_name="Bob")
    assert ratio.you_name == "Alice"
    assert ratio.you_wins == 16
    assert ratio.friend_wins == 22


def test_parse_win_ratio_youl_ocr_variant_identifies_viewer() -> None:
    lines = [
        "Bob (friend)",
        "Win ratio",
        "Alice",
        "10",
        "youl",
        "7",
    ]
    ratio = _parse_win_ratio(lines, friend_name="Bob")
    assert ratio.you_name == "Alice"


def test_parsers_module_contains_no_sample_player_names() -> None:
    source = PARSERS_SOURCE.read_text(encoding="utf-8").lower()
    assert "vilemaxim" not in source
