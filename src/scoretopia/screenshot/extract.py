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


def compare_extraction_to_expected(
    result: ExtractionResult,
    expected: dict[str, Any],
) -> tuple[bool, str]:
    """Compare extraction against expected JSON structure.

    Returns (matched, message). Message is a pass note or a readable diff.
    Comparison is structural (parsed JSON), not raw string equality.
    """
    actual = serialize_extraction(result)
    expected_norm = _json_normalize(expected)
    if actual == expected_norm:
        return True, "Extraction matches expected JSON."

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
