"""Canonical Polytopia tribe names and OCR fuzzy resolution."""

from __future__ import annotations

import difflib
import re

CANONICAL_TRIBES: tuple[str, ...] = (
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
)

# Minimum SequenceMatcher ratio to accept a fuzzy OCR correction.
_FUZZY_MATCH_THRESHOLD = 0.65


def _normalize_for_matching(raw: str) -> str:
    """Lowercase and strip spaces/hyphens for tribe comparison."""
    return re.sub(r"[\s-]+", "", raw.strip().lower())


_NORMALIZED_TRIBES: dict[str, str] = {
    _normalize_for_matching(tribe): tribe for tribe in CANONICAL_TRIBES
}
_NORMALIZED_NAMES: list[str] = list(_NORMALIZED_TRIBES)


def resolve_ocr_tribe(raw: str) -> str:
    """Map OCR tribe text to the closest canonical name, or return raw."""
    normalized = _normalize_for_matching(raw)
    if not normalized:
        return raw

    if normalized in _NORMALIZED_TRIBES:
        return _NORMALIZED_TRIBES[normalized]

    best_match = difflib.get_close_matches(
        normalized,
        _NORMALIZED_NAMES,
        n=1,
        cutoff=_FUZZY_MATCH_THRESHOLD,
    )
    if not best_match:
        return raw

    return _NORMALIZED_TRIBES[best_match[0]]
