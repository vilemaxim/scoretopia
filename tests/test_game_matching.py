"""Tests for fuzzy participant-set matching (Task 008)."""

from __future__ import annotations

import pytest

from scoretopia.domain.matching import (
    human_participant_key,
    normalize_participant_name,
    participant_sets_match,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Diremouse01", "diremouse01"),
        ("DiremouseO1", "diremouse01"),
        ("DiremouseO]", "diremouse01"),
        ("  Lord Union 409  ", "lord union 409"),
    ],
)
def test_normalize_participant_name_applies_ocr_corrections(
    raw: str,
    expected: str,
) -> None:
    assert normalize_participant_name(raw) == expected


def test_human_participant_key_excludes_bot_names() -> None:
    key = human_participant_key(("Alice", "Bob", "Crazy Bot", "Hard Bot"))

    assert key == frozenset({"alice", "bob"})


def test_participant_sets_match_when_ocr_variants_equivalent() -> None:
    game_start_names = ("Diremouse01", "Lord Union 409", "vilemaxim")
    game_end_names = ("DiremouseO1", "Lord Union 409", "vilemaxim")

    assert participant_sets_match(game_start_names, game_end_names)


def test_participant_sets_match_ignores_bots_in_either_set() -> None:
    stored = ("Alice", "Bob", "Crazy Bot")
    incoming = ("Alice", "Bob")

    assert participant_sets_match(stored, incoming)


def test_participant_sets_do_not_match_when_human_sets_differ() -> None:
    assert not participant_sets_match(("Alice", "Bob"), ("Alice", "Carol"))
    assert not participant_sets_match(("Alice",), ("Alice", "Bob"))
