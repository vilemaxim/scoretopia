"""Fuzzy participant-set matching for game lifecycle."""

from __future__ import annotations

from collections.abc import Iterable

from scoretopia.screenshot.parsers import _normalize_ocr_name


def is_bot_name(name: str) -> bool:
    return name.strip().lower().endswith(" bot")


def normalize_participant_name(name: str) -> str:
    """Normalize a participant name for fuzzy set comparison."""
    return _normalize_ocr_name(name).lower()


def human_participant_key(names: Iterable[str]) -> frozenset[str]:
    """Build a comparison key from human participant names, excluding bots."""
    return frozenset(
        normalize_participant_name(name)
        for name in names
        if not is_bot_name(name)
    )


def participant_sets_match(
    stored_names: Iterable[str],
    incoming_names: Iterable[str],
) -> bool:
    """Return True when two participant lists refer to the same human set."""
    return human_participant_key(stored_names) == human_participant_key(incoming_names)
