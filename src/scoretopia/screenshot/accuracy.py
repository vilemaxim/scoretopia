"""Per-field OCR accuracy across local golden pairs.

Compares each extraction field against its same-stem ``.json`` golden and
prints counts only — never field values — so the output carries no player
names or game details from the samples.

Player names are first resolved against a roster, as production resolves
them against the players table: the roster is every name in the goldens, and
an OCR name takes a roster spelling only when ``match_known_name`` finds
exactly one.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from scoretopia.screenshot.extract import (
    DEFAULT_MODEL_DIR,
    extract_screenshot,
    serialize_extraction,
)
from scoretopia.screenshot.models import (
    ExtractionResult,
    GameBasicsExtraction,
    GameEndExtraction,
)
from scoretopia.screenshot.name_matching import match_known_name, player_names_match

DEFAULT_SAMPLES_DIR = Path("samples") / "screenshots"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
_NAME_FIELDS = frozenset({"name", "winner", "friend_name", "you_name"})

FieldResult = tuple[str, bool]


def discover_golden_pairs(samples_dir: Path) -> list[tuple[Path, Path]]:
    """Return (image, json) pairs where both files exist under samples_dir."""
    if not samples_dir.is_dir():
        return []
    pairs: list[tuple[Path, Path]] = []
    for path in sorted(samples_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        golden = path.with_suffix(".json")
        if golden.is_file():
            pairs.append((path, golden))
    return pairs


def roster_from_goldens(pairs: Iterable[tuple[Path, Path]]) -> list[str]:
    """Every player and winner name in the goldens, sorted and de-duplicated."""
    names: set[str] = set()
    for _image, golden in pairs:
        expected = json.loads(golden.read_text(encoding="utf-8"))
        if isinstance(expected.get("winner"), str):
            names.add(expected["winner"])
        for player in expected.get("players") or []:
            if isinstance(player, dict) and isinstance(player.get("name"), str):
                names.add(player["name"])
    return sorted(names)


def resolve_extraction_names(
    result: ExtractionResult, roster: Sequence[str]
) -> ExtractionResult:
    """Replace OCR player names (and the winner) with their roster spelling."""

    def resolve(name: str) -> str:
        return match_known_name(name, roster) or name

    if isinstance(result, GameBasicsExtraction):
        return replace(
            result,
            players=tuple(replace(p, name=resolve(p.name)) for p in result.players),
        )
    if isinstance(result, GameEndExtraction):
        return replace(
            result,
            winner=resolve(result.winner) if result.winner else result.winner,
            players=tuple(replace(p, name=resolve(p.name)) for p in result.players),
        )
    return result


def _values_match(
    key: str, actual: Any, expected: Any, *, screenshot_type: str
) -> bool:
    if key in _NAME_FIELDS and isinstance(expected, str):
        if not isinstance(actual, str):
            return False
        return player_names_match(actual, expected, screenshot_type=screenshot_type)
    return actual == expected


def _compare_mapping(
    prefix: str,
    actual: dict[str, Any] | None,
    expected: dict[str, Any],
    *,
    screenshot_type: str,
) -> list[FieldResult]:
    results: list[FieldResult] = []
    for key, expected_val in expected.items():
        if key == "screenshot_type":
            continue
        field = f"{prefix}.{key}"
        actual_val = actual.get(key) if actual is not None else None
        if key == "players" and isinstance(expected_val, list):
            results.extend(
                _compare_players(
                    field,
                    actual_val if actual is not None else None,
                    expected_val,
                    screenshot_type=screenshot_type,
                )
            )
        elif isinstance(expected_val, dict):
            results.extend(
                _compare_mapping(
                    field,
                    actual_val if isinstance(actual_val, dict) else None,
                    expected_val,
                    screenshot_type=screenshot_type,
                )
            )
        else:
            ok = actual is not None and _values_match(
                key, actual_val, expected_val, screenshot_type=screenshot_type
            )
            results.append((field, ok))
    return results


def _compare_players(
    field: str,
    actual: Any,
    expected: list[Any],
    *,
    screenshot_type: str,
) -> list[FieldResult]:
    actual_list = actual if isinstance(actual, list) else None
    results: list[FieldResult] = [
        (
            f"{field}.count",
            actual_list is not None and len(actual_list) == len(expected),
        )
    ]
    for index, expected_player in enumerate(expected):
        if not isinstance(expected_player, dict):
            continue
        actual_player = None
        if actual_list is not None and index < len(actual_list):
            candidate = actual_list[index]
            actual_player = candidate if isinstance(candidate, dict) else None
        results.extend(
            _compare_mapping(
                field,
                actual_player,
                expected_player,
                screenshot_type=screenshot_type,
            )
        )
    return results


def field_results(
    actual: dict[str, Any] | None,
    expected: dict[str, Any],
) -> list[FieldResult]:
    """Return one (field, correct) row per expected field.

    Player fields yield one row per player. A missing extraction, or one of
    the wrong screenshot type, counts every expected field as wrong.
    """
    screenshot_type = str(expected.get("screenshot_type"))
    type_ok = actual is not None and actual.get("screenshot_type") == screenshot_type
    rows = [("screenshot_type", type_ok)]
    rows.extend(
        _compare_mapping(
            screenshot_type,
            actual if type_ok else None,
            expected,
            screenshot_type=screenshot_type,
        )
    )
    return rows


class FieldTally:
    """Running correct/total counts per field."""

    def __init__(self) -> None:
        self._counts: dict[str, list[int]] = {}

    def add(self, results: Iterable[FieldResult]) -> None:
        for field, ok in results:
            entry = self._counts.setdefault(field, [0, 0])
            entry[0] += int(ok)
            entry[1] += 1

    @property
    def counts(self) -> dict[str, tuple[int, int]]:
        return {field: (c[0], c[1]) for field, c in self._counts.items()}


def format_accuracy(
    tally: FieldTally,
    *,
    samples: int,
    failed: int,
    roster: int | None = None,
) -> str:
    """Render per-field counts as aligned text (field names and counts only)."""
    counts = tally.counts
    width = max((len(field) for field in counts), default=0)
    roster_note = "off" if roster is None else f"{roster} names"
    lines = [
        f"samples: {samples}  extraction failed: {failed}  roster: {roster_note}",
        "",
    ]
    for field in sorted(counts):
        correct, total = counts[field]
        lines.append(f"  {field.ljust(width)}  {correct}/{total}")
    correct_sum = sum(c for c, _ in counts.values())
    total_sum = sum(t for _, t in counts.values())
    lines.append("")
    lines.append(f"total: {correct_sum}/{total_sum}")
    return "\n".join(lines) + "\n"


def run_accuracy(
    samples_dir: Path,
    *,
    model_dir: Path = DEFAULT_MODEL_DIR,
    roster: Sequence[str] | None = None,
) -> tuple[FieldTally, int, int]:
    """Extract every golden pair and tally per-field results.

    Names are resolved against ``roster`` first when one is given. Returns
    (tally, samples, failed), where failed counts images whose extraction
    raised (e.g. unrecognized screenshot type).
    """
    tally = FieldTally()
    samples = 0
    failed = 0
    for image_path, golden_path in discover_golden_pairs(samples_dir):
        samples += 1
        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        actual: dict[str, Any] | None
        try:
            result = extract_screenshot(image_path, model_dir=model_dir)
            if roster is not None:
                result = resolve_extraction_names(result, roster)
            actual = serialize_extraction(result)
        except ValueError:
            failed += 1
            actual = None
        tally.add(field_results(actual, expected))
    return tally, samples, failed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print per-field OCR accuracy across golden pairs "
            "(image + same-stem .json). Prints counts only."
        )
    )
    parser.add_argument(
        "samples_dir",
        type=Path,
        nargs="?",
        default=DEFAULT_SAMPLES_DIR,
        help="Directory of golden pairs (default: samples/screenshots)",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help="Directory for EasyOCR model files",
    )
    parser.add_argument(
        "--no-roster",
        action="store_true",
        help="Compare raw OCR names, without resolving them against the goldens' names",
    )
    args = parser.parse_args(argv)
    roster = (
        None
        if args.no_roster
        else roster_from_goldens(discover_golden_pairs(args.samples_dir))
    )
    tally, samples, failed = run_accuracy(
        args.samples_dir, model_dir=args.model_dir, roster=roster
    )
    print(
        format_accuracy(
            tally,
            samples=samples,
            failed=failed,
            roster=None if roster is None else len(roster),
        ),
        end="",
    )


if __name__ == "__main__":
    main()
