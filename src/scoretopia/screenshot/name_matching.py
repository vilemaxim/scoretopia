"""Fuzzy player-name comparison for OCR extraction."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable

_FUZZY_MATCH_THRESHOLD = 0.80
_MIN_PREFIX_LEN = 3


def normalize_ocr_name(name: str) -> str:
    """Fix common OCR misreads in Polytopia player names."""
    cleaned = name.strip()
    cleaned = cleaned.replace("O]", "01").replace("O1", "01")
    cleaned = cleaned.rstrip("]")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


# Glyphs OCR swaps for one another in small player-name text: dots vanish,
# and 1 / l / I / | and 0 / O are read interchangeably.
_OCR_KEY_FOLDS = str.maketrans({"1": "l", "I": "l", "|": "l", "0": "o", "O": "o"})


def ocr_name_key(name: str) -> str:
    """Comparison key that ignores the glyph confusions OCR makes in names."""
    return normalize_ocr_name(name).replace(".", "").translate(_OCR_KEY_FOLDS).lower()


def match_known_name(raw: str, known_names: Iterable[str]) -> str | None:
    """Return the one known name whose OCR key equals raw's, else None.

    None when no known name matches, or when more than one does.
    """
    key = ocr_name_key(raw)
    if not key:
        return None
    matches = {name for name in known_names if ocr_name_key(name) == key}
    return matches.pop() if len(matches) == 1 else None


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
