"""Parse OCR text from Polytopia screenshot types."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scoretopia.screenshot.models import (
    FriendProfileExtraction,
    GameEndExtraction,
    GameEndHeader,
    GameEndPlayer,
    WinRatio,
)
from scoretopia.screenshot.name_matching import (
    normalize_ocr_name as _normalize_ocr_name,
)
from scoretopia.screenshot.name_matching import (
    player_names_match,
)
from scoretopia.screenshot.tribes import resolve_ocr_tribe


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    y: float
    x: float


def _parse_int(value: str) -> int | None:
    digits = re.sub(r"[^\d-]", "", value)
    if not digits or digits == "-":
        return None
    return int(digits)


def _sorted_lines(results: list[OCRLine]) -> list[OCRLine]:
    return sorted(results, key=lambda item: (item.y, item.x))


def _line_texts(results: list[OCRLine]) -> list[str]:
    return [item.text.strip() for item in _sorted_lines(results) if item.text.strip()]


_ONGOING_LIST_MESSAGE = (
    "Ongoing games list is not supported; upload an in-game "
    "Game Basics screenshot or a pre-game lobby screenshot."
)

_WINNER_LINE_PATTERN = re.compile(
    r"^([A-Za-z][\w\s]*?)\s+wins?\b",
    re.IGNORECASE,
)
_WINST_LINE_PATTERN = re.compile(
    r"^([A-Za-z][\w\s]*?)\s+winst\b",
    re.IGNORECASE,
)
_WAITING_TO_PLAY_PATTERN = re.compile(r"waiting for .+ to play")


def _winner_line_match(line: str) -> re.Match[str] | None:
    return _WINNER_LINE_PATTERN.search(line) or _WINST_LINE_PATTERN.search(line)


def detect_screenshot_type(results: list[OCRLine]) -> str:
    lines = _line_texts(results)
    text = "\n".join(lines).lower()
    if "friend list" in text or "add friend" in text:
        return "friend_profile"
    if _looks_like_game_basics(text):
        return "game_basics"
    if _looks_like_ongoing_list(text):
        raise ValueError(_ONGOING_LIST_MESSAGE)
    if _looks_like_game_end(lines, text):
        return "game_end"
    raise ValueError(
        "Unrecognized screenshot type; expected game end, "
        "friend profile, or game basics"
    )


def _looks_like_ongoing_list(text: str) -> bool:
    has_header = "multiplayer" in text and "ongoing" in text
    has_card_actions = "back" in text and "open" in text
    return has_header and has_card_actions and _WAITING_TO_PLAY_PATTERN.search(text)


def _looks_like_game_end(lines: list[str], text: str) -> bool:
    if "resigned on turn" in text:
        return True
    return any(_winner_line_match(line) for line in lines)


def _looks_like_game_basics(text: str) -> bool:
    has_game_timer = "game timer" in text
    has_score_mode = "glory" in text or "might" in text
    has_in_game_controls = "resign" in text or "leave" in text
    has_lobby_status = (
        "waiting" in text and "accept" in text and "invitation" in text
    )
    return has_game_timer and has_score_mode and (
        has_in_game_controls or has_lobby_status
    )


def parse_game_end(results: list[OCRLine]) -> GameEndExtraction:
    lines = _line_texts(results)
    joined = "\n".join(lines)

    winner = _extract_winner(lines)
    header = _extract_game_end_header(results, joined)
    players = _extract_game_end_players(results, winner)
    return GameEndExtraction(winner=winner, header=header, players=tuple(players))


def _extract_winner(lines: list[str]) -> str | None:
    for line in lines:
        match = _winner_line_match(line)
        if match:
            return _normalize_ocr_name(match.group(1))
    return None


def _extract_game_end_header(results: list[OCRLine], joined: str) -> GameEndHeader:
    score_match = re.search(r"score:\s*(\d[\d,]*)\s*points", joined, re.IGNORECASE)
    score = _parse_int(score_match.group(1)) if score_match else None
    if score is None:
        for item in _sorted_lines(results):
            if item.y > 400:
                continue
            if re.fullmatch(r"[\d,]+", item.text.strip()):
                value = _parse_int(item.text)
                if value and value >= 1000:
                    score = value
                    break

    stars = None
    stars_gained = None
    header_items = [item for item in results if item.y < 400]
    for item in header_items:
        stars_header = re.search(r"stars\s*\(\+(\d+)\)", item.text, re.IGNORECASE)
        if stars_header:
            stars_gained = int(stars_header.group(1))
    star_candidates = [
        _parse_int(item.text)
        for item in header_items
        if re.fullmatch(r"\d{2,3}", item.text.strip())
    ]
    star_candidates = [value for value in star_candidates if value is not None]
    if star_candidates:
        stars = max(star_candidates)

    turn = None
    turns = [
        int(match.group(1))
        for match in re.finditer(r"resigned on turn (\d+)", joined, re.IGNORECASE)
    ]
    if turns:
        turn = max(turns)
    else:
        for item in header_items:
            if re.fullmatch(r"\d{1,3}", item.text.strip()):
                value = _parse_int(item.text)
                if value is not None and 1 <= value <= 200:
                    turn = value

    return GameEndHeader(
        score=score,
        stars=stars,
        stars_gained=stars_gained,
        turn=turn,
    )


def _extract_game_end_players(
    results: list[OCRLine], winner: str | None
) -> list[GameEndPlayer]:
    detail_lines = [
        item
        for item in _sorted_lines(results)
        if re.search(r"score:\s*\d", item.text, re.IGNORECASE)
        or "resigned on turn" in item.text.lower()
    ]

    players: list[GameEndPlayer] = []
    for detail in detail_lines:
        block = [
            item
            for item in results
            if (detail.y - 130) <= item.y <= (detail.y + 15) and item.text.strip()
        ]
        block.sort(key=lambda item: (item.y, item.x))
        texts = [item.text.strip() for item in block]

        tribe = None
        status = None
        score = None
        detail_match = re.match(
            r"^(?P<tribe>[^,;]+)[,;]\s*(?P<rest>.+)$",
            detail.text.strip(),
            re.IGNORECASE,
        )
        if detail_match:
            tribe = resolve_ocr_tribe(detail_match.group("tribe").strip())
            rest = detail_match.group("rest").strip()
            score_match = re.search(
                r"score:\s*(\d[\d,]*)\s*points", rest, re.IGNORECASE
            )
            if score_match:
                score = _parse_int(score_match.group(1))
            elif "resigned on turn" in rest.lower():
                status = rest

        name = _pick_player_name(texts, detail.text)
        elo_change, elo = _pick_elo_fields(texts)
        players.append(
            GameEndPlayer(
                name=name or "Unknown",
                tribe=tribe,
                status=status,
                score=score,
                elo_change=elo_change,
                elo=elo,
                is_winner=_names_match(name, winner),
            )
        )
    return players


def _pick_player_name(texts: list[str], detail_text: str) -> str | None:
    candidates: list[str] = []
    for text in texts:
        lower = text.lower()
        if text == detail_text:
            continue
        if lower == "elo":
            continue
        if re.fullmatch(r"[+-]?\d[\d,]*", text):
            continue
        if re.search(r"score:\s*\d", text, re.IGNORECASE):
            continue
        if "resigned on turn" in lower and ";" in text:
            continue
        if re.search(r"\bwins?\b|\bwinst\b", lower):
            continue
        if len(text) >= 3:
            candidates.append(text)

    if not candidates:
        return None

    # Prefer the longest name-like token in the block.
    best = max(candidates, key=len)
    return _normalize_ocr_name(best)


def _pick_elo_fields(texts: list[str]) -> tuple[int | None, int | None]:
    elo_change: int | None = None
    elo: int | None = None
    for text in texts:
        if re.fullmatch(r"[+-]\d{1,2}", text):
            elo_change = _parse_int(text)
        elif re.fullmatch(r"[\d,]{3,5}", text):
            value = _parse_int(text)
            if value is not None and value >= 100:
                elo = value
    return elo_change, elo


def _names_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return player_names_match(left, right, screenshot_type="game_end")


def parse_friend_profile(results: list[OCRLine]) -> FriendProfileExtraction:
    lines = _line_texts(results)
    joined = "\n".join(lines)

    friend_name = None
    title_match = re.search(
        r"^(.+?)\s*\(friend\)", joined, re.MULTILINE | re.IGNORECASE
    )
    if title_match:
        friend_name = title_match.group(1).strip()

    alias = _match_label_value(joined, r"Alias:\s*(.+)")
    num_friends = _match_label_int(joined, r'n["\u00ba\u00b0]?\s*of friends:\s*(\d+)')
    games_played = _match_label_int(joined, r"Games Played:\s*(\d+)")
    game_version = _match_label_int(joined, r"Game version:\s*(\d+)")
    elo = _match_label_int(joined, r"Elo:\s*([\d,]+)")

    win_ratio = _parse_win_ratio(lines, friend_name)
    return FriendProfileExtraction(
        friend_name=friend_name,
        alias=alias,
        num_friends=num_friends,
        games_played=games_played,
        game_version=game_version,
        elo=elo,
        win_ratio=win_ratio,
    )


def _match_label_value(joined: str, pattern: str) -> str | None:
    match = re.search(pattern, joined, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _match_label_int(joined: str, pattern: str) -> int | None:
    match = re.search(pattern, joined, re.IGNORECASE)
    if match:
        return _parse_int(match.group(1))
    return None


def _is_win_ratio_name_line(line: str) -> bool:
    return (
        not re.fullmatch(r"\d{1,3}", line)
        and line.lower() not in {"you", "youl", "back", "new game", "elo"}
        and len(line) > 2
    )


def _parse_win_ratio(lines: list[str], friend_name: str | None) -> WinRatio:
    you_name: str | None = None
    friend_wins: int | None = None
    you_wins: int | None = None

    win_ratio_idx = next(
        (idx for idx, line in enumerate(lines) if "win ratio" in line.lower()),
        None,
    )
    if win_ratio_idx is None:
        return WinRatio()

    segment = lines[win_ratio_idx + 1 : win_ratio_idx + 8]
    numbers = [int(line) for line in segment if re.fullmatch(r"\d{1,3}", line)]

    if len(numbers) >= 2:
        you_wins, friend_wins = numbers[0], numbers[1]
    elif len(numbers) == 1:
        friend_wins = numbers[0]

    you_token_idx = next(
        (
            idx
            for idx, line in enumerate(segment)
            if line.lower() in {"you", "youl"}
        ),
        None,
    )
    if you_token_idx is not None:
        for idx in range(you_token_idx - 1, -1, -1):
            line = segment[idx]
            if _is_win_ratio_name_line(line):
                you_name = _normalize_ocr_name(line)
                break

    return WinRatio(
        you_name=you_name,
        you_wins=you_wins,
        friend_name=friend_name,
        friend_wins=friend_wins,
    )

