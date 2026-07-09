"""Extract structured Polytopia data from screenshot images."""

from __future__ import annotations

import json
from dataclasses import asdict
from difflib import unified_diff
from pathlib import Path
from typing import TYPE_CHECKING, Any

import easyocr

from scoretopia.screenshot.game_basics import parse_game_basics
from scoretopia.screenshot.models import ExtractionResult
from scoretopia.screenshot.name_matching import player_names_match
from scoretopia.screenshot.parsers import (
    OCRLine,
    detect_screenshot_type,
    parse_friend_profile,
    parse_game_end,
)

if TYPE_CHECKING:
    from scoretopia.screenshot.models import (
        FriendProfileExtraction,
        GameBasicsExtraction,
        GameEndExtraction,
    )

DEFAULT_MODEL_DIR = Path(".easyocr_models")


def extract_screenshot(
    image_path: str | Path,
    *,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
) -> ExtractionResult:
    """Run OCR on a Polytopia screenshot and return structured data."""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Screenshot not found: {path}")

    reader = easyocr.Reader(
        ["en"],
        gpu=False,
        verbose=False,
        model_storage_directory=str(model_dir),
    )
    raw_results = reader.readtext(str(path))
    ocr_results = [
        OCRLine(
            text=text,
            confidence=confidence,
            y=(bbox[0][1] + bbox[2][1]) / 2,
            x=(bbox[0][0] + bbox[2][0]) / 2,
        )
        for bbox, text, confidence in raw_results
    ]

    screenshot_type = detect_screenshot_type(ocr_results)
    if screenshot_type == "game_end":
        return parse_game_end(ocr_results)
    if screenshot_type == "game_basics":
        return parse_game_basics(ocr_results, path)
    return parse_friend_profile(ocr_results)


def format_extraction(result: ExtractionResult) -> str:
    """Render an extraction result as human-readable text."""
    if result.screenshot_type == "game_end":
        return _format_game_end(result)
    if result.screenshot_type == "game_basics":
        return _format_game_basics(result)
    return _format_friend_profile(result)


def serialize_extraction(result: ExtractionResult) -> dict[str, Any]:
    """Convert an ExtractionResult to a JSON-serializable dict.

    Nested tuples (e.g. players) become lists so the shape matches JSON
    files produced by ``json.dumps``.
    """
    return json.loads(json.dumps(asdict(result)))


def _json_normalize(value: Any) -> Any:
    """Round-trip through JSON so compare ignores non-JSON container types."""
    return json.loads(json.dumps(value))


def compare_extraction_player_names(
    result: ExtractionResult,
    expected: dict[str, Any],
) -> tuple[bool, str]:
    """Compare only ``players[].name`` lists between extraction and expected JSON.

    Ignores score, tribe, elo, header, and other non-name fields.
    Uses ``player_names_match`` with the screenshot type from the extraction.
    """
    actual = serialize_extraction(result)
    screenshot_type = actual.get("screenshot_type")
    expected_type = expected.get("screenshot_type")
    if screenshot_type != expected_type:
        return (
            False,
            f"screenshot_type mismatch: actual={screenshot_type!r} "
            f"expected={expected_type!r}",
        )

    actual_players = actual.get("players")
    expected_players = expected.get("players")
    if not isinstance(actual_players, list) or not isinstance(expected_players, list):
        return False, "players field missing or not a list in actual or expected JSON"

    if len(actual_players) != len(expected_players):
        return (
            False,
            f"player count mismatch: actual={len(actual_players)} "
            f"expected={len(expected_players)}",
        )

    mode = (
        "exact normalized"
        if screenshot_type == "game_basics"
        else "fuzzy + prefix"
    )
    for index, (actual_player, expected_player) in enumerate(
        zip(actual_players, expected_players, strict=True)
    ):
        if not isinstance(actual_player, dict) or not isinstance(expected_player, dict):
            return False, f"player record at index {index} is not an object"
        actual_name = str(actual_player.get("name") or "")
        expected_name = str(expected_player.get("name") or "")
        if not player_names_match(
            actual_name,
            expected_name,
            screenshot_type=str(screenshot_type),
        ):
            return (
                False,
                f"player name mismatch at index {index}: "
                f"actual={actual_name!r} expected={expected_name!r} "
                f"(match mode: {mode})",
            )

    return True, "Player names match expected."


def compare_extraction_to_expected(
    result: ExtractionResult,
    expected: dict[str, Any],
) -> tuple[bool, str]:
    """Compare extraction against expected JSON structure.

    Returns (matched, message). Message is a pass note or a readable diff.
    Comparison is structural (parsed JSON), not raw string equality.
    Player ``name`` fields (and game-end ``winner``) use ``player_names_match``.
    """
    actual = serialize_extraction(result)
    expected_norm = _json_normalize(expected)
    if actual == expected_norm:
        return True, "Extraction matches expected JSON."
    if _extractions_match_with_fuzzy_names(actual, expected_norm):
        return True, "Extraction matches expected JSON (player names fuzzy-matched)."

    actual_text = json.dumps(actual, indent=2, sort_keys=True) + "\n"
    expected_text = json.dumps(expected_norm, indent=2, sort_keys=True) + "\n"
    diff = "".join(
        unified_diff(
            expected_text.splitlines(keepends=True),
            actual_text.splitlines(keepends=True),
            fromfile="expected",
            tofile="actual",
        )
    )
    message = "Extraction does not match expected JSON.\n" + (
        diff if diff else f"actual={actual!r}\nexpected={expected_norm!r}"
    )
    return False, message


def _extractions_match_with_fuzzy_names(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    screenshot_type = actual.get("screenshot_type")
    if screenshot_type != expected.get("screenshot_type"):
        return False
    if set(actual) != set(expected):
        return False

    for key, expected_val in expected.items():
        actual_val = actual[key]
        if key == "players":
            if not isinstance(actual_val, list) or not isinstance(expected_val, list):
                return False
            if len(actual_val) != len(expected_val):
                return False
            if not all(
                _player_record_matches(
                    actual_player,
                    expected_player,
                    screenshot_type=str(screenshot_type),
                )
                for actual_player, expected_player in zip(
                    actual_val, expected_val, strict=True
                )
            ):
                return False
        elif key == "winner" and screenshot_type == "game_end":
            if not _winner_names_match(actual_val, expected_val):
                return False
        elif actual_val != expected_val:
            return False
    return True


def _winner_names_match(actual: Any, expected: Any) -> bool:
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return actual == expected
    return player_names_match(str(actual), str(expected), screenshot_type="game_end")


def _player_record_matches(
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    screenshot_type: str,
) -> bool:
    if set(actual) != set(expected):
        return False
    for key, expected_val in expected.items():
        actual_val = actual[key]
        if key == "name":
            if not player_names_match(
                str(actual_val or ""),
                str(expected_val or ""),
                screenshot_type=screenshot_type,
            ):
                return False
        elif actual_val != expected_val:
            return False
    return True


def _format_game_end(result: GameEndExtraction) -> str:
    lines = ["Polytopia Game End Screenshot", "=" * 32, ""]
    if result.winner:
        lines.append(f"Winner: {result.winner}")
        lines.append("")

    header = result.header
    lines.append("Match summary:")
    if header.score is not None:
        lines.append(f"  Score: {header.score:,}")
    if header.stars is not None:
        stars_line = f"  Stars: {header.stars}"
        if header.stars_gained is not None:
            stars_line += f" (+{header.stars_gained})"
        lines.append(stars_line)
    if header.turn is not None:
        lines.append(f"  Turn: {header.turn}")
    lines.append("")

    lines.append("Players:")
    for player in result.players:
        marker = " (winner)" if player.is_winner else ""
        lines.append(f"  - {player.name}{marker}")
        if player.tribe:
            lines.append(f"      Tribe: {player.tribe}")
        if player.score is not None:
            lines.append(f"      Score: {player.score:,} points")
        if player.status:
            lines.append(f"      Status: {player.status}")
        if player.elo_change is not None or player.elo is not None:
            change = (
                f"{player.elo_change:+d}"
                if player.elo_change is not None
                else "?"
            )
            elo = f"{player.elo:,}" if player.elo is not None else "?"
            lines.append(f"      Elo: {change} -> {elo}")
    return "\n".join(lines) + "\n"


def _format_game_basics(result: GameBasicsExtraction) -> str:
    lines = ["Polytopia Game Basics Screenshot", "=" * 34, ""]
    if result.game_name:
        lines.append(f"Game: {result.game_name}")
        lines.append("")

    lines.append("Settings:")
    if result.map_size is not None:
        lines.append(f"  Map size: {result.map_size}")
    if result.terrain:
        lines.append(f"  Terrain: {result.terrain}")
    if result.game_type:
        lines.append(f"  Type: {result.game_type}")
    if result.target_score is not None:
        lines.append(f"  Target score: {result.target_score:,}")
    if result.game_timer:
        lines.append(f"  Game timer: {result.game_timer}")
    lines.append("")

    if result.win_condition_text:
        lines.append(f"Win condition: {result.win_condition_text}")
    if result.turn_status:
        lines.append(f"Turn status: {result.turn_status}")
    lines.append("")

    lines.append("Players:")
    for player in result.players:
        markers: list[str] = []
        if player.is_you:
            markers.append("you")
        if player.is_eliminated:
            markers.append("eliminated")
        suffix = f" ({', '.join(markers)})" if markers else ""
        lines.append(f"  - {player.name}{suffix}")
    return "\n".join(lines) + "\n"


def _format_friend_profile(result: FriendProfileExtraction) -> str:
    lines = ["Polytopia Friend Profile Screenshot", "=" * 35, ""]
    if result.friend_name:
        lines.append(f"Friend: {result.friend_name}")
    if result.alias:
        lines.append(f"Alias: {result.alias}")
    lines.append("")
    lines.append("Profile:")
    if result.num_friends is not None:
        lines.append(f"  Number of friends: {result.num_friends}")
    if result.games_played is not None:
        lines.append(f"  Games played: {result.games_played}")
    if result.game_version is not None:
        lines.append(f"  Game version: {result.game_version}")
    if result.elo is not None:
        lines.append(f"  Elo: {result.elo:,}")
    lines.append("")

    ratio = result.win_ratio
    if any([ratio.you_name, ratio.friend_name, ratio.you_wins, ratio.friend_wins]):
        lines.append("Win ratio (head-to-head):")
        you = ratio.you_name or "You"
        friend = ratio.friend_name or result.friend_name or "Friend"
        you_wins = ratio.you_wins if ratio.you_wins is not None else "?"
        friend_wins = ratio.friend_wins if ratio.friend_wins is not None else "?"
        lines.append(f"  {you}: {you_wins}")
        lines.append(f"  {friend}: {friend_wins}")
    return "\n".join(lines) + "\n"


def write_extraction(
    image_path: str | Path,
    output_path: str | Path,
    *,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
) -> ExtractionResult:
    """Extract screenshot data and write formatted text to output_path."""
    result = extract_screenshot(image_path, model_dir=model_dir)
    text = format_extraction(result)
    out = Path(output_path)
    out.write_text(text, encoding="utf-8")
    return result


__all__ = [
    "compare_extraction_player_names",
    "compare_extraction_to_expected",
    "extract_screenshot",
    "format_extraction",
    "player_names_match",
    "serialize_extraction",
    "write_extraction",
]
