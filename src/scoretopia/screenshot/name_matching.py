"""Fuzzy player-name comparison for OCR extraction."""

from __future__ import annotations

import difflib
import re

_FUZZY_MATCH_THRESHOLD = 0.80
_MIN_PREFIX_LEN = 3


def normalize_ocr_name(name: str) -> str:
    """Fix common OCR misreads in Polytopia player names."""
    cleaned = name.strip()
    cleaned = cleaned.replace("O]", "01").replace("O1", "01")
    cleaned = cleaned.rstrip("]")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def player_names_match(actual: str, expected: str, *, screenshot_type: str) -> bool:
    """Compare OCR player names with type-specific tolerance."""
    if not actual or not expected:
        return False

    left = normalize_ocr_name(actual).lower()
    right = normalize_ocr_name(expected).lower()

    if screenshot_type == "game_basics":
        return left == right

    if screenshot_type == "game_end":
        if left == right:
            return True
        if _prefix_match(left, right):
            return True
        shorter_len = min(len(left), len(right))
        if shorter_len < _MIN_PREFIX_LEN:
            return False
        return (
            difflib.SequenceMatcher(None, left, right).ratio()
            >= _FUZZY_MATCH_THRESHOLD
        )

    return left == right


def _prefix_match(left: str, right: str) -> bool:
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < _MIN_PREFIX_LEN:
        return False
    return longer.startswith(shorter)
