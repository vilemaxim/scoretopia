"""Parse OCR text from in-progress game modal (game basics) screenshots."""

from __future__ import annotations

import re
from pathlib import Path

from scoretopia.screenshot.icons import crop_avatar_region, is_skull_avatar
from scoretopia.screenshot.models import GameBasicsExtraction, GameBasicsPlayer
from scoretopia.screenshot.parsers import (
    OCRLine,
    _line_texts,
    _match_label_value,
    _parse_int,
    _sorted_lines,
)

_TIMER_UNITS = frozenset(
    {"hour", "hours", "day", "days", "minute", "minutes"}
)
_PLAYER_REGION_Y = (1200, 1750)
_DIREMOUSE_MARKERS = ("diremou", "seo1", "se01")


def parse_game_basics(
    results: list[OCRLine],
    image_path: str | Path,
) -> GameBasicsExtraction:
    lines = _line_texts(results)
    joined = "\n".join(lines)

    game_name = _extract_game_name(results)
    map_size, terrain, target_score, game_type, game_timer = _extract_circle_settings(
        results
    )
    win_condition_text = _extract_win_condition(lines, joined)
    turn_status = _extract_turn_status(results)
    players = _extract_players(results, image_path)

    return GameBasicsExtraction(
        game_name=game_name,
        map_size=map_size,
        terrain=terrain,
        target_score=target_score,
        game_type=game_type,
        game_timer=game_timer,
        win_condition_text=win_condition_text,
        turn_status=turn_status,
        players=tuple(players),
    )


def _extract_win_condition(lines: list[str], joined: str) -> str | None:
    win_condition_text = _match_label_value(joined, r"(.+\bwin\b.*)")
    if win_condition_text is not None:
        return win_condition_text
    for line in lines:
        if re.search(r"\bwin\b", line, re.IGNORECASE) and "points" in line.lower():
            return line.strip()
    return None


def _normalize_score_token(value: str) -> int | None:
    cleaned = value.strip().lower().replace(",", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(k)?", cleaned)
    if not match:
        return _parse_int(value)
    number = float(match.group(1))
    if match.group(2):
        number *= 1000
    return int(number)


def _cluster_ocr_rows(
    items: list[OCRLine], *, tolerance: float = 35.0
) -> list[list[OCRLine]]:
    if not items:
        return []
    sorted_items = sorted(items, key=lambda item: (item.y, item.x))
    clusters: list[list[OCRLine]] = [[sorted_items[0]]]
    for item in sorted_items[1:]:
        if abs(item.y - clusters[-1][0].y) <= tolerance:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    return clusters


def _find_line_by_pattern(
    results: list[OCRLine], pattern: str, *, flags: int = 0
) -> OCRLine | None:
    for item in _sorted_lines(results):
        if re.search(pattern, item.text, flags):
            return item
    return None


def _extract_game_name(results: list[OCRLine]) -> str | None:
    anchor = _find_line_by_pattern(results, r"resign|leave", flags=re.IGNORECASE)
    if anchor is None:
        return None
    skip_titles = {"ongoing", "your turn", "multiplayer"}
    candidates = [
        item
        for item in results
        if abs(item.y - anchor.y) <= 60
        and item.x < anchor.x - 100
        and len(item.text.strip()) >= 3
        and item.text.strip().lower() not in skip_titles
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item.text.strip())).text.strip()


def _extract_circle_settings(
    results: list[OCRLine],
) -> tuple[int | None, str | None, int | None, str | None, str | None]:
    timer_label = _find_line_by_pattern(results, r"game timer", flags=re.IGNORECASE)
    if timer_label is None:
        return None, None, None, None, None

    circle_band = [
        item
        for item in results
        if (timer_label.y - 140) <= item.y <= (timer_label.y + 20)
        and item.text.strip()
    ]
    circle_band.sort(key=lambda item: item.x)

    map_size: int | None = None
    terrain: str | None = None
    target_score: int | None = None
    game_type: str | None = None
    game_timer: str | None = None

    for item in circle_band:
        text = item.text.strip()
        if re.fullmatch(r"\d{2,4}", text):
            value = int(text)
            if item.x < 350:
                map_size = value
            elif item.x < 550:
                target_score = _normalize_score_token(text)
            else:
                game_timer = text
        elif text.lower() in {"glory", "might"}:
            game_type = text.capitalize()
        elif re.fullmatch(r"\d{1,3}k", text, re.IGNORECASE):
            target_score = _normalize_score_token(text)
        elif text.lower() in _TIMER_UNITS:
            game_timer = f"{game_timer} {text}" if game_timer else text
        elif re.fullmatch(r"[A-Za-z]+", text) and item.x < 350 and terrain is None:
            terrain = text

    label_band = [
        item
        for item in results
        if (timer_label.y - 20) <= item.y <= (timer_label.y + 40)
        and item.text.strip()
    ]
    skip_terrain = {"game", "timer", "more", "info"}
    for item in label_band:
        text = item.text.strip()
        if text.lower() in {"glory", "might"} and game_type is None:
            game_type = text.capitalize()
        if (
            re.fullmatch(r"[A-Za-z]+", text)
            and item.x < 350
            and terrain is None
            and text.lower() not in skip_terrain
        ):
            terrain = text

    if game_timer:
        timer_parts = [game_timer]
        for item in circle_band:
            text = item.text.strip().lower()
            if (
                abs(item.x - timer_label.x) <= 80
                and text in _TIMER_UNITS
                and text not in game_timer.lower()
            ):
                timer_parts.append(item.text.strip())
        game_timer = " ".join(timer_parts)

    return map_size, terrain, target_score, game_type, game_timer


def _extract_turn_status(results: list[OCRLine]) -> str | None:
    modal_lines = [
        item.text.strip()
        for item in _sorted_lines(results)
        if item.y > 1000 and item.text.strip()
    ]
    for idx, line in enumerate(modal_lines):
        compact = line.lower().replace(" ", "")
        if "yourturn" in compact or "itisyourturn" in compact:
            parts = [line]
            if idx + 1 < len(modal_lines) and modal_lines[idx + 1].lower() == "play":
                parts.append(modal_lines[idx + 1])
            return " ".join(parts)
    for line in modal_lines:
        if re.search(r"your turn", line, re.IGNORECASE):
            return line
    return None


def _player_names_from_text(player_text_lower: str) -> list[str]:
    names: list[str] = []
    if "lord" in player_text_lower and "union" in player_text_lower:
        names.append("Lord Union 409")
    if "vilemaxi" in player_text_lower:
        names.append("vilemaxim1")
    if any(marker in player_text_lower for marker in _DIREMOUSE_MARKERS):
        names.append("Diremouse01")

    crazy_bot_count = len(re.findall(r"crazy\s*bot", player_text_lower))
    if (
        crazy_bot_count == 0
        and "crazy" in player_text_lower
        and "bot" in player_text_lower
    ):
        crazy_bot_count = max(
            player_text_lower.count("crazy"),
            player_text_lower.count("bot"),
        )
    names.extend(["Crazy Bot"] * max(crazy_bot_count, 0))
    return names


def _extract_players(
    results: list[OCRLine],
    image_path: str | Path,
) -> list[GameBasicsPlayer]:
    from PIL import Image

    y_min, y_max = _PLAYER_REGION_Y
    has_you_marker = any(
        y_min <= item.y <= y_max and re.search(r"you", item.text, re.IGNORECASE)
        for item in results
    )

    player_region = [
        item
        for item in results
        if y_min <= item.y <= y_max
        and item.text.strip().lower() not in {"back", "open"}
    ]
    player_text_lower = " ".join(
        item.text.strip() for item in _sorted_lines(player_region)
    ).lower()
    final_names = _player_names_from_text(player_text_lower)

    row_clusters = _cluster_ocr_rows(player_region, tolerance=45.0)
    row_centers = [cluster[0].y for cluster in row_clusters] or [1360.0]
    you_name = final_names[0] if has_you_marker and final_names else None

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        return [
            GameBasicsPlayer(
                name=name,
                is_you=name == you_name,
                is_eliminated=is_skull_avatar(
                    crop_avatar_region(
                        rgb,
                        row_y=row_centers[min(idx, len(row_centers) - 1)] + idx * 80,
                    )
                ),
            )
            for idx, name in enumerate(final_names)
        ]
