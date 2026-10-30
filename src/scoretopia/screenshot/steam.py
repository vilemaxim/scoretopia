"""Steam build (landscape frame) layout for Polytopia screenshots.

The Steam client draws the same Multiplayer card and victory screen as the
phone app, but in a fixed landscape frame (1280x720 under the steam-test
capture) with much smaller text. The phone parsers anchor on absolute phone
coordinates, so this module anchors on the panel's own labels instead.

Two stages:

* ``refine_*`` re-reads the small text from magnified crops of the frame and
  swaps those readings in for the full-frame ones. It needs the image and an
  OCR callable.
* ``parse_*`` turns the refined ``OCRLine`` list into an extraction. It is
  pure, so unit tests drive it with synthetic OCR output.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Callable, Iterable

import numpy as np
from scipy import ndimage

from scoretopia.screenshot.models import (
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndHeader,
    GameEndPlayer,
)
from scoretopia.screenshot.name_matching import (
    normalize_ocr_name,
    player_names_match,
)
from scoretopia.screenshot.parsers import OCRLine
from scoretopia.screenshot.tribes import resolve_ocr_tribe

# OCR over an RGB crop; returns lines in the crop's own pixel coordinates.
ReadFn = Callable[[np.ndarray, "str | None"], list[OCRLine]]
# Whether the avatar centred at (x, y) in the frame is an elimination skull.
SkullFn = Callable[[float, float], bool]

CROP_SCALE = 3
DIGIT_SCALE = 4
VOTE_SCALES = (3, 4, 5)
DIGITS = "0123456789"

MAP_TYPES = ("Dryland", "Lakes", "Pangea", "Continents", "Archipelago", "Water World")
TIMER_UNITS = ("minutes", "hours", "days")
# The Steam GAME SETUP screen offers these turn limits besides Live Game
# (steam-test capture, 2026-10-18). The small timer digits misread easily, so
# a readable unit picks the option.
TIMER_OPTIONS = ("24 hours", "7 days")
GAME_TYPES = ("Glory", "Might")

# Card geometry, relative to the circle-label row (y) and the Glory/Might
# label (x). Measured on steam-test captures, 1280x720.
_CIRCLE_PITCH = 73.0
_CIRCLE_HALF_WIDTH = 26.0
_CIRCLE_VALUE_Y = (-58.0, -22.0)
_TIMER_NUMBER_Y = (-56.0, -34.0)
_TIMER_UNIT_Y = (-33.0, -17.0)
_TITLE_Y = (-100.0, -70.0)
_PANEL_HALF_WIDTH = 170.0
_BUTTON_X_MIN = 120.0
_TEXT_Y = (20.0, 75.0)
# Player names sit between the avatars and the BACK/OPEN buttons.
_NAME_BAND_Y = (-95.0, -40.0)
_NAME_BAND_FALLBACK_BUTTON_DY = 213.0
_NAME_LINE_GAP = 6.0
_NAME_COLUMN_TOLERANCE = 35.0
_YOU_ABOVE_NAME = (35.0, 75.0)
_AVATAR_ABOVE_NAME = 33.0

# Victory screen geometry, relative to each row's "elo" chip.
_ELO_ROW_TOLERANCE = 10.0
_ELO_NAME_MAX_DX = -60.0
_ELO_VALUE_MIN_DX = 20.0
_ELO_CHANGE_Y = (-35.0, -13.0)
_ELO_CHANGE_X = (50.0, 95.0)
_DETAIL_Y = (8.0, 28.0)
_HEADER_MAX_Y = 35.0
_HEADER_VALUE_Y = (10.0, 35.0)
# The crop stops above the bright bar under the header.
_HEADER_CROP_Y = (10.0, 31.0)
_HEADER_HALF_WIDTH = 30.0
_WINNER_ROW_TOLERANCE = 10.0
_ROWS_TOP_ABOVE_WINNER = 25.0
_ROWS_BOTTOM_BELOW_ELO = 30.0

_BUTTON_WORDS = ("resign", "leave", "decline")
_ELO_PATTERN = re.compile(r"^e[l1i][o0]$", re.IGNORECASE)
_WIN_PATTERN = re.compile(r"\s+win\w*\W*$", re.IGNORECASE)
_SCORE_PATTERN = re.compile(r"\bs[a-z]{2,4}\W*(\d[\d,]*)", re.IGNORECASE)
_RESIGNED_PATTERN = re.compile(
    r"resig\w*.*?t[uv]?(?:rn|m)\W*(?:[a-z]{1,2}\W+)??([0-9OoDQ]\d*)",
    re.IGNORECASE,
)
_FIRST_TO_REACH_PATTERN = re.compile(
    r"first\s*to\s*reach\s*([\d,.]+)\s*points", re.IGNORECASE
)
_SCORE_CIRCLE_PATTERN = re.compile(r"(\d{1,3})\s*k", re.IGNORECASE)
_YOUR_TURN_PATTERN = re.compile(r"it\s*is\s*your\s*turn", re.IGNORECASE)
_WAITING_PATTERN = re.compile(r"waiting\s+for\s+.+\s+to\s+play", re.IGNORECASE)
_STARS_GAINED_PATTERN = re.compile(r"\(\s*\+\s*(\d+)\s*\)")


def is_steam_frame(size: tuple[int, int]) -> bool:
    """True for a landscape frame: the Steam build, not a phone screenshot."""
    width, height = size
    return width > height


# --- small helpers ---------------------------------------------------------


def _text(item: OCRLine) -> str:
    return item.text.strip()


def _fuzzy(text: str, choices: Iterable[str], cutoff: float = 0.6) -> str | None:
    lowered = {choice.lower(): choice for choice in choices}
    match = difflib.get_close_matches(text.strip().lower(), lowered, n=1, cutoff=cutoff)
    return lowered[match[0]] if match else None


def _in_box(item: OCRLine, box: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = box
    return left <= item.x <= right and top <= item.y <= bottom


def _row(items: Iterable[OCRLine], y: float, tolerance: float) -> list[OCRLine]:
    return sorted(
        (item for item in items if abs(item.y - y) <= tolerance and _text(item)),
        key=lambda item: item.x,
    )


def _join(items: Iterable[OCRLine]) -> str:
    return " ".join(_text(item) for item in sorted(items, key=lambda i: i.x)).strip()


def _parse_number(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _ocr_digit_fix(text: str) -> str:
    """Map letters OCR commonly reads for digits inside a numeric token."""
    return text.translate(str.maketrans({"S": "5", "s": "5", "O": "0", "o": "0"}))


# --- detection -------------------------------------------------------------


def detect_steam_screenshot_type(results: list[OCRLine]) -> str | None:
    """Classify a Steam frame by its layout cues; None when neither matches."""
    texts = [_text(item).lower() for item in results]
    joined = "\n".join(texts)
    elo_chips = sum(1 for text in texts if _ELO_PATTERN.match(text))
    if elo_chips >= 2 and any(text == "done" for text in texts):
        return "game_end"
    has_mode = any(_fuzzy(text, GAME_TYPES, 0.75) for text in texts)
    if has_mode and ("more info" in joined or "game timer" in joined):
        return "game_basics"
    return None


# --- game basics (Multiplayer card, lobby, replay panel) -------------------


def _label_anchor(results: list[OCRLine]) -> tuple[float, float] | None:
    """Return (label row y, Glory/Might label x) for the card's circle row."""
    for item in results:
        if _fuzzy(_text(item), GAME_TYPES, 0.75):
            return item.y, item.x
    for item in results:
        if "game timer" in _text(item).lower():
            return item.y, item.x - _CIRCLE_PITCH
    return None


def _button_row_y(results: list[OCRLine], label_y: float) -> float:
    backs = [
        item.y for item in results if _text(item).lower() == "back" and item.y > label_y
    ]
    return min(backs) if backs else label_y + _NAME_BAND_FALLBACK_BUTTON_DY


def _tokens_near(
    results: list[OCRLine],
    *,
    x: float,
    y_range: tuple[float, float],
    half_width: float = _CIRCLE_HALF_WIDTH,
) -> list[OCRLine]:
    return [
        item
        for item in results
        if abs(item.x - x) <= half_width
        and y_range[0] <= item.y <= y_range[1]
        and _text(item)
    ]


def _game_name(results: list[OCRLine], label_y: float, mode_x: float) -> str | None:
    title = [
        item
        for item in results
        if label_y + _TITLE_Y[0] <= item.y <= label_y + _TITLE_Y[1]
        and abs(item.x - mode_x) <= _PANEL_HALF_WIDTH
        and item.x - mode_x < _BUTTON_X_MIN
        and not _fuzzy(_text(item), _BUTTON_WORDS, 0.7)
    ]
    words = [
        re.sub(r"[._,]+$", "", _text(item)) for item in sorted(title, key=lambda i: i.x)
    ]
    name = " ".join(word for word in words if word)
    return name or None


def _target_score(
    results: list[OCRLine], win_text: str | None, label_y: float, mode_x: float
) -> int | None:
    if win_text:
        match = _FIRST_TO_REACH_PATTERN.search(win_text)
        if match:
            return _parse_number(match.group(1))
    circle = _tokens_near(
        results,
        x=mode_x,
        y_range=(label_y + _CIRCLE_VALUE_Y[0], label_y + _CIRCLE_VALUE_Y[1]),
    )
    for item in circle:
        match = _SCORE_CIRCLE_PATTERN.search(_ocr_digit_fix(_text(item)))
        if match:
            return int(match.group(1)) * 1000
    return None


def _game_timer(results: list[OCRLine], label_y: float, mode_x: float) -> str | None:
    timer_x = mode_x + _CIRCLE_PITCH
    labels = _tokens_near(results, x=timer_x, y_range=(label_y - 8, label_y + 8))
    if not any("timer" in _text(item).lower() for item in labels):
        return None
    numbers = [
        _parse_number(_ocr_digit_fix(_text(item)))
        for item in _tokens_near(
            results,
            x=timer_x,
            y_range=(label_y + _TIMER_NUMBER_Y[0], label_y + _TIMER_NUMBER_Y[1]),
        )
    ]
    number = next((value for value in numbers if value is not None), None)
    units = [
        _fuzzy(_text(item), TIMER_UNITS, 0.5)
        for item in _tokens_near(
            results,
            x=timer_x,
            y_range=(label_y + _TIMER_UNIT_Y[0], label_y + _TIMER_UNIT_Y[1]),
        )
    ]
    unit = next((value for value in units if value), None)
    if number is not None and unit and f"{number} {unit}" in TIMER_OPTIONS:
        return f"{number} {unit}"
    by_unit = [option for option in TIMER_OPTIONS if unit and option.endswith(unit)]
    if len(by_unit) == 1:
        return by_unit[0]
    if number is None:
        return None
    return f"{number} {unit}" if unit else str(number)


def _panel_lines(results: list[OCRLine], label_y: float, mode_x: float) -> list[str]:
    return [
        _text(item)
        for item in sorted(results, key=lambda i: (i.y, i.x))
        if label_y + _TEXT_Y[0] <= item.y <= label_y + _TEXT_Y[1]
        and abs(item.x - mode_x) <= _PANEL_HALF_WIDTH
        and _text(item)
    ]


def _name_columns(
    band: list[OCRLine],
) -> list[tuple[str, float, float]]:
    """Pair wrapped name lines into (name, column x, first-line y)."""
    tokens = [
        item
        for item in band
        if re.search(r"[A-Za-z0-9]", _text(item))
        and _text(item).lower() not in {"add", "you"}
    ]
    if not tokens:
        return []
    first_y = min(item.y for item in tokens)
    first = sorted(
        (item for item in tokens if item.y - first_y <= _NAME_LINE_GAP),
        key=lambda item: item.x,
    )
    rest = [item for item in tokens if item.y - first_y > _NAME_LINE_GAP]

    columns = [[item] for item in first]
    for item in sorted(rest, key=lambda i: i.x):
        nearest = min(columns, key=lambda col: abs(col[0].x - item.x))
        if abs(nearest[0].x - item.x) <= _NAME_COLUMN_TOLERANCE:
            nearest.append(item)

    names: list[tuple[str, float, float]] = []
    for column in columns:
        parts = [re.sub(r"^[^A-Za-z0-9]+", "", _text(item)) for item in column]
        name = normalize_ocr_name("".join(parts))
        if len(name) < 2:
            continue
        column_x = column[-1].x if len(column) > 1 else column[0].x
        names.append((name, column_x, column[0].y))
    return names


def parse_steam_game_basics(
    results: list[OCRLine],
    *,
    is_skull: SkullFn | None = None,
) -> GameBasicsExtraction:
    """Parse a Steam Multiplayer card, lobby, invitation, or replay panel."""
    anchor = _label_anchor(results)
    if anchor is None:
        return GameBasicsExtraction()
    label_y, mode_x = anchor

    mode_labels = [
        _fuzzy(_text(item), GAME_TYPES, 0.75)
        for item in _tokens_near(results, x=mode_x, y_range=(label_y - 8, label_y + 8))
    ]
    mode_label = next((label for label in mode_labels if label), None)

    size_x = mode_x - _CIRCLE_PITCH
    size_values = [
        int(_text(item))
        for item in _tokens_near(
            results,
            x=size_x,
            y_range=(label_y + _CIRCLE_VALUE_Y[0], label_y + _CIRCLE_VALUE_Y[1]),
        )
        if re.fullmatch(r"\d{2,4}", _text(item))
    ]
    map_size = size_values[0] if size_values else None
    terrain_tokens = _tokens_near(
        results, x=size_x, y_range=(label_y - 8, label_y + 8), half_width=45
    )
    terrain = None
    if terrain_tokens:
        raw_terrain = _join(terrain_tokens)
        terrain = _fuzzy(raw_terrain, MAP_TYPES) or raw_terrain

    lines = _panel_lines(results, label_y, mode_x)
    win_line = next(
        (line for line in lines if _FIRST_TO_REACH_PATTERN.search(line)), None
    )
    target_score = _target_score(results, win_line, label_y, mode_x)
    win_condition_text = (
        f"First to reach {target_score:,} points win"
        if win_line and target_score is not None
        else win_line
    )

    turn_status = None
    for line in lines:
        if _YOUR_TURN_PATTERN.search(line):
            turn_status = "It is your turn to Play"
            break
        if _WAITING_PATTERN.search(line):
            turn_status = line
            break

    buttons_y = _button_row_y(results, label_y)
    band = [
        item
        for item in results
        if buttons_y + _NAME_BAND_Y[0] <= item.y <= buttons_y + _NAME_BAND_Y[1]
        and abs(item.x - mode_x) <= _PANEL_HALF_WIDTH
    ]
    columns = _name_columns(band)
    you_x: float | None = None
    if columns:
        name_y = columns[0][2]
        you_marks = [
            item
            for item in results
            if _text(item).lower().startswith("you")
            and _YOU_ABOVE_NAME[0] <= name_y - item.y <= _YOU_ABOVE_NAME[1]
        ]
        if you_marks:
            you_x = you_marks[0].x
    you_index = (
        min(range(len(columns)), key=lambda idx: abs(columns[idx][1] - you_x))
        if you_x is not None and columns
        else None
    )
    players = tuple(
        GameBasicsPlayer(
            name=name,
            is_you=index == you_index,
            is_eliminated=bool(
                is_skull and is_skull(column_x, name_y - _AVATAR_ABOVE_NAME)
            ),
        )
        for index, (name, column_x, name_y) in enumerate(columns)
    )

    return GameBasicsExtraction(
        game_name=_game_name(results, label_y, mode_x),
        map_size=map_size,
        terrain=terrain,
        target_score=target_score,
        game_type=mode_label,
        game_timer=_game_timer(results, label_y, mode_x),
        win_condition_text=win_condition_text,
        turn_status=turn_status,
        players=players,
    )


# --- game end (victory screen) ---------------------------------------------


def _elo_chips(results: list[OCRLine]) -> list[OCRLine]:
    return sorted(
        (item for item in results if _ELO_PATTERN.match(_text(item))),
        key=lambda item: item.y,
    )


def _header(results: list[OCRLine]) -> GameEndHeader:
    labels = [item for item in results if item.y <= _HEADER_MAX_Y]

    def label(prefix: str) -> OCRLine | None:
        return next(
            (item for item in labels if _text(item).lower().startswith(prefix)), None
        )

    def value_under(item: OCRLine | None) -> int | None:
        if item is None:
            return None
        values = [
            other
            for other in results
            if abs(other.x - item.x) <= _HEADER_HALF_WIDTH
            and item.y + _HEADER_VALUE_Y[0] <= other.y <= item.y + _HEADER_VALUE_Y[1]
            and re.fullmatch(r"[\d,]+", _text(other))
        ]
        if not values:
            return None
        return _parse_number(_text(min(values, key=lambda o: abs(o.x - item.x))))

    stars_label = label("star")
    stars_gained = None
    if stars_label is not None:
        match = _STARS_GAINED_PATTERN.search(_text(stars_label))
        if match:
            stars_gained = int(match.group(1))
    return GameEndHeader(
        score=value_under(label("score")),
        stars=value_under(stars_label),
        stars_gained=stars_gained,
        turn=value_under(label("turn")),
    )


def _resigned_turn(detail: str) -> int | None:
    match = _RESIGNED_PATTERN.search(detail)
    if not match:
        return None
    return int(_ocr_digit_fix(match.group(1)).replace("D", "0").replace("Q", "0"))


def _player_row(results: list[OCRLine], chip: OCRLine) -> GameEndPlayer:
    row = _row(results, chip.y, _ELO_ROW_TOLERANCE)
    name_tokens = [
        item
        for item in row
        if item.x - chip.x <= _ELO_NAME_MAX_DX and re.search(r"[A-Za-z]", _text(item))
    ]
    name = normalize_ocr_name(re.sub(r"[.\s]+$", "", _join(name_tokens)))
    elo_values = [
        _parse_number(_text(item))
        for item in row
        if item.x - chip.x >= _ELO_VALUE_MIN_DX and re.fullmatch(r"[\d,]+", _text(item))
    ]
    elo = elo_values[0] if elo_values else None
    changes = [
        int(_text(item))
        for item in results
        if chip.y + _ELO_CHANGE_Y[0] <= item.y <= chip.y + _ELO_CHANGE_Y[1]
        and item.x - chip.x >= _ELO_CHANGE_X[0] - 20
        and re.fullmatch(r"[+-]\d+", _text(item))
    ]
    detail = _join(
        item
        for item in results
        if chip.y + _DETAIL_Y[0] <= item.y <= chip.y + _DETAIL_Y[1] and item.x < chip.x
    )

    tribe = None
    status = None
    score = None
    if detail:
        head = re.split(r"[,;]|\s+s[a-z]{2,4}\W", detail, maxsplit=1)[0]
        head = re.split(r"\s+resig", head, maxsplit=1)[0].strip()
        if head:
            tribe = resolve_ocr_tribe(head)
        if re.search(r"resig", detail, re.IGNORECASE):
            turn = _resigned_turn(detail)
            status = f"resigned on turn {turn}" if turn is not None else "resigned"
        else:
            match = _SCORE_PATTERN.search(detail[len(head) :])
            if match:
                score = _parse_number(match.group(1))

    return GameEndPlayer(
        name=name or "Unknown",
        tribe=tribe,
        status=status,
        score=score,
        elo_change=changes[0] if changes else None,
        elo=elo,
    )


def _winner_line(results: list[OCRLine], first_chip_y: float) -> str | None:
    for item in sorted(results, key=lambda i: i.y):
        if item.y >= first_chip_y - 30:
            break
        row_text = _join(_row(results, item.y, _WINNER_ROW_TOLERANCE))
        if _WIN_PATTERN.search(row_text):
            return normalize_ocr_name(_WIN_PATTERN.sub("", row_text))
    return None


def parse_steam_game_end(results: list[OCRLine]) -> GameEndExtraction:
    """Parse a Steam victory screen (the one reached through Finish Game)."""
    chips = _elo_chips(results)
    players = [_player_row(results, chip) for chip in chips]
    raw_winner = _winner_line(results, chips[0].y) if chips else None

    winner_index: int | None = None
    if raw_winner:
        winner_index = next(
            (
                idx
                for idx, player in enumerate(players)
                if player_names_match(
                    player.name, raw_winner, screenshot_type="game_end"
                )
            ),
            None,
        )
    if winner_index is None and players and raw_winner is None:
        # The victory screen lists the winner first.
        winner_index = 0

    final = tuple(
        GameEndPlayer(
            name=player.name,
            tribe=player.tribe,
            status=player.status,
            score=player.score,
            elo_change=player.elo_change,
            elo=player.elo,
            is_winner=idx == winner_index,
        )
        for idx, player in enumerate(players)
    )
    winner = players[winner_index].name if winner_index is not None else raw_winner
    return GameEndExtraction(
        winner=winner,
        header=_header(results),
        players=final,
    )


# --- refinement: re-read small text from magnified crops --------------------


Box = tuple[int, int, int, int]


def _crop(rgb: np.ndarray, box: Box) -> np.ndarray:
    left, top, right, bottom = box
    height, width = rgb.shape[:2]
    return rgb[max(top, 0) : min(bottom, height), max(left, 0) : min(right, width)]


def _read_scaled(
    read: ReadFn,
    crop: np.ndarray,
    box: Box,
    *,
    scale: int,
    allowlist: str | None = None,
) -> list[OCRLine]:
    from PIL import Image

    if crop.size == 0:
        return []
    image = Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8))
    image = image.resize(
        (image.width * scale, image.height * scale), Image.Resampling.LANCZOS
    )
    lines = read(np.asarray(image), allowlist)
    return [
        OCRLine(
            text=line.text,
            confidence=line.confidence,
            y=line.y / scale + box[1],
            x=line.x / scale + box[0],
        )
        for line in lines
    ]


def mask_platform_icons(crop: np.ndarray, *, min_pixels: int = 12) -> np.ndarray:
    """Black out the platform icons (pure white or green) drawn before names.

    Name text is anti-aliased grey; the icons are solid pure-white or green
    glyphs, which OCR otherwise reads as a stray leading letter.
    """
    rgb = crop.astype(int)
    white = rgb.min(axis=2) >= 250
    green = (rgb[:, :, 1] >= 120) & (rgb[:, :, 0] < 90) & (rgb[:, :, 2] < 90)
    labels, count = ndimage.label(white | green, structure=np.ones((3, 3)))
    out = rgb.copy()
    for index in range(1, count + 1):
        ys, xs = np.nonzero(labels == index)
        if len(ys) < min_pixels:
            continue
        out[
            max(ys.min() - 1, 0) : ys.max() + 2,
            max(xs.min() - 1, 0) : xs.max() + 2,
        ] = 0
    return out


def mask_colored(crop: np.ndarray, *, ratio: float = 0.35) -> np.ndarray:
    """Black out saturated pixels (laurels, stars) so only neutral text remains."""
    rgb = crop.astype(float)
    peak = rgb.max(axis=2)
    saturation = (peak - rgb.min(axis=2)) / np.maximum(peak, 1)
    out = rgb.copy()
    out[(saturation > ratio) & (peak > 0.3 * max(peak.max(), 1))] = 0
    return out


def relative_contrast(gray: np.ndarray) -> np.ndarray:
    """Stretch dim text against its own background (header under an overlay)."""
    background = float(np.median(gray))
    span = max(float(gray.max()) - background, 1.0)
    stretched = np.clip((gray - background) / span, 0.0, 1.0) * 255.0
    return np.repeat(stretched[:, :, None], 3, axis=2)


def glyph_count(gray: np.ndarray, *, level: float = 0.4, min_width: int = 2) -> int:
    """Count glyphs as runs of lit columns, against the crop's background.

    Runs narrower than ``min_width`` are stray pixels, not digits.
    """
    background = float(np.median(gray))
    span = float(gray.max()) - background
    if span <= 0:
        return 0
    return column_runs((gray - background) / span > level, min_width=min_width)


def column_runs(mask: np.ndarray, *, min_pixels: int = 1, min_width: int = 1) -> int:
    """Count runs of columns holding at least ``min_pixels`` set pixels."""
    lit = mask.sum(axis=0) >= min_pixels
    edges = np.diff(np.concatenate(([0], lit.astype(int), [0])))
    starts = np.nonzero(edges == 1)[0]
    ends = np.nonzero(edges == -1)[0]
    return int(((ends - starts) >= min_width).sum())


def _replace(results: list[OCRLine], box: Box, refined: list[OCRLine]) -> list[OCRLine]:
    left, top, right, bottom = box
    kept = [
        item
        for item in results
        if not (left <= item.x <= right and top <= item.y <= bottom)
    ]
    return kept + refined


def refine_steam_game_basics(
    results: list[OCRLine], rgb: np.ndarray, read: ReadFn
) -> list[OCRLine]:
    """Re-read the card's circle values and player names from magnified crops."""
    anchor = _label_anchor(results)
    if anchor is None:
        return results
    label_y, mode_x = anchor
    refined = list(results)

    def circle_box(x: float, y_range: tuple[float, float]) -> Box:
        return (
            int(x - _CIRCLE_HALF_WIDTH),
            int(label_y + y_range[0]),
            int(x + _CIRCLE_HALF_WIDTH),
            int(label_y + y_range[1]),
        )

    size_box = circle_box(mode_x - _CIRCLE_PITCH, _CIRCLE_VALUE_Y)
    refined = _replace(
        refined,
        size_box,
        _read_scaled(
            read, _crop(rgb, size_box), size_box, scale=DIGIT_SCALE, allowlist=DIGITS
        ),
    )
    score_box = circle_box(mode_x, _CIRCLE_VALUE_Y)
    refined = _replace(
        refined,
        score_box,
        _read_scaled(
            read,
            mask_colored(_crop(rgb, score_box)),
            score_box,
            scale=DIGIT_SCALE,
            allowlist=DIGITS + "k",
        ),
    )
    timer_x = mode_x + _CIRCLE_PITCH
    number_box = circle_box(timer_x, _TIMER_NUMBER_Y)
    unit_box = circle_box(timer_x, _TIMER_UNIT_Y)
    refined = _replace(
        refined,
        (number_box[0], number_box[1], number_box[2], unit_box[3]),
        _read_scaled(
            read,
            _crop(rgb, number_box),
            number_box,
            scale=DIGIT_SCALE,
            allowlist=DIGITS,
        )
        + _read_scaled(
            read,
            _crop(rgb, unit_box),
            unit_box,
            scale=DIGIT_SCALE,
            allowlist="abcdefghijklmnopqrstuvwxyz",
        ),
    )

    buttons_y = _button_row_y(results, label_y)
    band_box = (
        int(mode_x - _PANEL_HALF_WIDTH),
        int(buttons_y + _NAME_BAND_Y[0]),
        int(mode_x + _PANEL_HALF_WIDTH),
        int(buttons_y + _NAME_BAND_Y[1]),
    )
    names = _read_scaled(
        read, mask_platform_icons(_crop(rgb, band_box)), band_box, scale=CROP_SCALE
    )
    return _replace(refined, band_box, names)


def _header_value(
    results: list[OCRLine], rgb: np.ndarray, box: Box, read: ReadFn
) -> list[OCRLine]:
    """Read one header value; drop a leading star glyph by counting glyphs."""
    crop = _crop(rgb, box).astype(float)
    gray = crop.max(axis=2)
    saturation = (gray - crop.min(axis=2)) / np.maximum(gray, 1)
    colored = (saturation > 0.35) & (gray > 0.3 * max(gray.max(), 1))
    left = 0
    if colored.sum() >= 4:
        # The star's anti-aliased edge reaches past its coloured pixels.
        left = int(np.nonzero(colored.any(axis=0))[0].max()) + 3
    value = gray[:, left:]
    count = glyph_count(value)
    if count == 0:
        return []
    stretched = relative_contrast(value)
    padded = np.pad(stretched, ((6, 6), (8, 8), (0, 0)))
    digits = _vote_digits(read, (stretched, padded), count)
    if digits is None:
        # The full-frame reading keeps every glyph, the star included.
        for line in results:
            if _in_box(line, (box[0], box[1], box[2], box[3])):
                found = re.sub(r"\D", "", _text(line))
                if len(found) >= count:
                    digits = found[-count:]
                    break
    if digits is None:
        return []
    return [
        OCRLine(
            text=digits,
            confidence=1.0,
            y=(box[1] + box[3]) / 2,
            x=(box[0] + box[2]) / 2,
        )
    ]


def _vote_digits(read: ReadFn, crops: Iterable[np.ndarray], count: int) -> str | None:
    """Read a small number at several scales; return the majority reading.

    A lone small digit is found by OCR's text detector at some scales and not
    others, and misread at a few, so each crop is read at every scale in
    ``VOTE_SCALES`` and readings with fewer than ``count`` digits are dropped.
    """
    votes: Counter[str] = Counter()
    for crop in crops:
        height, width = crop.shape[:2]
        for scale in VOTE_SCALES:
            lines = _read_scaled(
                read, crop, (0, 0, width, height), scale=scale, allowlist=DIGITS
            )
            found = re.sub(
                r"\D",
                "",
                "".join(_text(line) for line in sorted(lines, key=lambda i: i.x)),
            )
            if len(found) >= count:
                votes[found[-count:]] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0]


def refine_steam_game_end(
    results: list[OCRLine], rgb: np.ndarray, read: ReadFn
) -> list[OCRLine]:
    """Re-read the victory header, player rows, and elo changes from crops."""
    chips = _elo_chips(results)
    if not chips:
        return results
    refined = list(results)

    done = [item.y for item in results if _text(item).lower() == "done"]
    rows_bottom = min(done) - 10 if done else chips[-1].y + _ROWS_BOTTOM_BELOW_ELO
    center_x = chips[0].x - 75
    rows_box = (
        int(center_x - _PANEL_HALF_WIDTH),
        int(chips[0].y - 60 - _ROWS_TOP_ABOVE_WINNER),
        int(center_x + _PANEL_HALF_WIDTH),
        int(rows_bottom),
    )
    refined = _replace(
        refined,
        rows_box,
        _read_scaled(read, _crop(rgb, rows_box), rows_box, scale=CROP_SCALE),
    )

    # Elo changes are small red/green digits: read them from the colour alone,
    # and take the sign from the hue (green +, red -).
    for chip in chips:
        box = (
            int(chip.x + _ELO_CHANGE_X[0]),
            int(chip.y + _ELO_CHANGE_Y[0]),
            int(chip.x + _ELO_CHANGE_X[1]),
            int(chip.y + _ELO_CHANGE_Y[1]),
        )
        crop = _crop(rgb, box).astype(int)
        saturated = (crop.max(axis=2) - crop.min(axis=2)) > 60
        if not saturated.any():
            refined = _replace(refined, box, [])
            continue
        red = crop[:, :, 0] > crop[:, :, 1] + 40
        sign = "-" if red[saturated].mean() > 0.5 else "+"
        glyphs = np.repeat(np.where(saturated, 255, 0)[:, :, None], 3, axis=2)
        # Count digit columns: three lit pixels per column drop the 1-2 px
        # minus bar and any coloured background edge bridging the glyphs,
        # but keep the plus sign's vertical bar, so a plus is one extra run.
        runs = column_runs(saturated, min_pixels=3)
        count = runs - 1 if sign == "+" else runs
        value = _vote_digits(read, (glyphs,), count) if count > 0 else None
        tokens = (
            [
                OCRLine(
                    text=f"{sign}{value}", confidence=1.0, y=chip.y - 24, x=box[0] + 20
                )
            ]
            if value is not None
            else []
        )
        refined = _replace(refined, box, tokens)

    # Header values sit dim under the overlay; the stars value has a gold
    # star glyph to its left that OCR reads as a digit.
    labels = [
        item
        for item in results
        if item.y <= _HEADER_MAX_Y
        and re.match(r"(score|star|turn)", _text(item).lower())
    ]
    for item in labels:
        box = (
            int(item.x - _HEADER_HALF_WIDTH),
            int(item.y + _HEADER_CROP_Y[0]),
            int(item.x + _HEADER_HALF_WIDTH),
            int(item.y + _HEADER_CROP_Y[1]),
        )
        refined = _replace(refined, box, _header_value(results, rgb, box, read))
    return refined
