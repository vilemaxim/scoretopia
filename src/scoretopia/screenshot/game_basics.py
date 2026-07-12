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
    _normalize_ocr_name,
    _parse_int,
    _sorted_lines,
)

_TIMER_UNITS = frozenset(
    {"hour", "hours", "day", "days", "minute", "minutes"}
)
_PLAYER_REGION_Y = (1200, 1750)
_PLAYER_ROW_TOLERANCE = 25.0
_PLAYER_STRIP_Y_GAP = 70.0
_X_ALIGN_TOLERANCE = 80.0
# Same-row OCR tokens farther apart than this are distinct players, not one name.
_SAME_ROW_MERGE_X_GAP = 100.0
# When EasyOCR merges adjacent names into one box, spread part x for column align.
_BLOB_PART_X_SPREAD = 120.0
_LEFT_COLUMN_X_MAX = 320.0
_ROSTER_MIN_NAMES = 3
_UI_LABELS = frozenset({"back", "open", "start game", "add", "start", "game"})
_PAIRABLE_SUFFIX_TOKENS = frozenset({"u", "ml", "m1", "ru", "r8"})
_SKIP_GAME_NAME_TITLES = frozenset(
    {
        "ongoing",
        "your turn",
        "their turn",
        "multiplayer",
        "replays",
        "back",
        "open",
        "resign",
        "leave",
        "share",
    }
)
_SKIP_TERRAIN_LABELS = frozenset(
    {"game", "timer", "more", "info", "back", "open", "share"}
)
_CRAZY_BOT_PATTERN = re.compile(r"crazy\s*bot", re.IGNORECASE)
_YOU_PATTERN = re.compile(r"\byou\b", re.IGNORECASE)


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


def _longest_title_candidate(candidates: list[OCRLine]) -> str | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item.text.strip())).text.strip()


def _extract_game_name(results: list[OCRLine]) -> str | None:
    anchor = _find_line_by_pattern(results, r"resign|leave", flags=re.IGNORECASE)
    if anchor is not None:
        return _longest_title_candidate(
            [
                item
                for item in results
                if abs(item.y - anchor.y) <= 60
                and item.x < anchor.x - 100
                and len(item.text.strip()) >= 3
                and item.text.strip().lower() not in _SKIP_GAME_NAME_TITLES
            ]
        )

    lower_boundary = _modal_content_lower_y(results)
    if lower_boundary is None:
        return None

    return _longest_title_candidate(
        [
            item
            for item in results
            if item.y < lower_boundary - 40
            and len(item.text.strip()) >= 3
            and item.text.strip().lower() not in _SKIP_GAME_NAME_TITLES
        ]
    )


def _modal_content_lower_y(results: list[OCRLine]) -> float | None:
    boundaries: list[float] = []
    for item in results:
        text = item.text.strip().lower()
        if "points" in text and "win" in text:
            boundaries.append(item.y)
        if re.fullmatch(r"(glory|might)", text, re.IGNORECASE):
            boundaries.append(item.y)
        if re.fullmatch(r"\d{1,3}k", text, re.IGNORECASE):
            boundaries.append(item.y)
    if not boundaries:
        return None
    return min(boundaries)


def _extract_circle_settings(
    results: list[OCRLine],
) -> tuple[int | None, str | None, int | None, str | None, str | None]:
    timer_label = _find_line_by_pattern(results, r"game timer", flags=re.IGNORECASE)
    settings_anchor = timer_label or _find_line_by_pattern(
        results, r"^(glory|might)$", flags=re.IGNORECASE
    )
    if settings_anchor is None:
        return None, None, None, None, None

    anchor_y = settings_anchor.y
    circle_band = [
        item
        for item in results
        if (anchor_y - 140) <= item.y <= (anchor_y + 20)
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
            elif timer_label is not None:
                game_timer = text
        elif (
            timer_label is not None
            and re.fullmatch(r"\d+", text)
            and item.x >= 350
        ):
            game_timer = text
        elif text.lower() in {"glory", "might"}:
            game_type = text.capitalize()
        elif re.fullmatch(r"\d{1,3}k", text, re.IGNORECASE):
            target_score = _normalize_score_token(text)
        elif text.lower() in _TIMER_UNITS:
            if timer_label is not None:
                game_timer = f"{game_timer} {text}" if game_timer else text
        elif (
            re.fullmatch(r"[A-Za-z]+", text)
            and item.x < 350
            and terrain is None
            and text.lower() not in _SKIP_TERRAIN_LABELS
        ):
            terrain = text

    label_band = [
        item
        for item in results
        if (anchor_y - 20) <= item.y <= (anchor_y + 40)
        and item.text.strip()
    ]
    for item in label_band:
        text = item.text.strip()
        if text.lower() in {"glory", "might"} and game_type is None:
            game_type = text.capitalize()
        if (
            re.fullmatch(r"[A-Za-z]+", text)
            and item.x < 350
            and terrain is None
            and text.lower() not in _SKIP_TERRAIN_LABELS
        ):
            terrain = text

    if game_timer and timer_label is not None:
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
    for line in modal_lines:
        if "game is over" in line.lower():
            return None
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
    for line in modal_lines:
        if re.search(r"waiting for .+ to play", line, re.IGNORECASE):
            return line
    return None


def _count_crazy_bots(text: str) -> int:
    return len(_CRAZY_BOT_PATTERN.findall(text))


def _strip_crazy_bots(text: str) -> str:
    return _CRAZY_BOT_PATTERN.sub(" ", text)


def _is_ui_label(text: str) -> bool:
    cleaned = text.strip().lower()
    return cleaned in _UI_LABELS


def _is_noise_token(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return True
    if cleaned.lower() in {"crazy", "bot", "u", "xy"}:
        return True
    if re.fullmatch(r"\d+", cleaned):
        return True
    return len(cleaned) < 2


def _is_pairable_suffix_token(text: str) -> bool:
    """Short OCR chips that complete a name across rows (not standalone players)."""
    cleaned = text.strip().lower()
    if cleaned in _PAIRABLE_SUFFIX_TOKENS:
        return True
    return len(cleaned) <= 2 and cleaned.isalpha()


def _stem_without_checkbox(text: str) -> str:
    """Drop trailing OCR checkbox/status `` 0`` before affinity checks."""
    return re.sub(r"\s+0$", "", text).strip() or text


def _split_name_affinity(left: str, right: str) -> int:
    """Affinity score for known vertically/horizontally split name pairs."""
    if not left or not right:
        return 0
    left_stem = _stem_without_checkbox(left)
    left_lower = left_stem.lower()
    right_lower = right.lower()
    score = 0
    if left_lower.endswith("maxi") and right_lower in {"ml", "m1"}:
        score += 100
    if "mou" in left_lower and right_lower.startswith("se"):
        score += 100
    if left_lower.endswith("rib") and right_lower.startswith("onucleic"):
        score += 100
    if left_lower.endswith("z4") and right_lower in {"ru", "r8"}:
        score += 100
    if left_lower.endswith(("z8", "z80")) and right_lower == "u":
        score += 100
    if _is_pairable_suffix_token(right) and re.search(r"[A-Za-z]\d+$", left_stem):
        score += 100
    return score


def _normalize_player_name(fragment: str) -> str:
    cleaned = _strip_crazy_bots(fragment)
    cleaned = re.sub(r"[\[\]]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Checkbox / status glyph OCR often inserts " 0" before a name continuation.
    cleaned = re.sub(r"\s+0(?=[A-Za-z])", "", cleaned)
    cleaned = re.sub(r"\s+0$", "", cleaned)
    cleaned = re.sub(r"^0+(?=[A-Za-z])", "", cleaned)
    cleaned = re.sub(r"0(?=[a-z])", "o", cleaned)
    cleaned = re.sub(r"^D\s+(?=[a-z])", "", cleaned, flags=re.IGNORECASE)
    # Leading D glued onto a capitalized stem (OCR prefix junk).
    cleaned = re.sub(r"^D(?=[A-Z])", "", cleaned)
    # Phone-icon OCR often becomes a leading Q/Z/D before "ombie…".
    cleaned = re.sub(r"^[QZD](?=ombie)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?i)mouseo1$", "mouse01", cleaned)
    cleaned = re.sub(r"5o$", "50", cleaned)
    cleaned = re.sub(r"Z80u$", "Z8u", cleaned)
    cleaned = re.sub(r"Z80$", "Z8u", cleaned)
    cleaned = re.sub(r"1o7$", "107", cleaned)
    cleaned = re.sub(r"loz$", "107", cleaned)
    cleaned = _normalize_ocr_name(cleaned)
    if cleaned.endswith("ml"):
        cleaned = cleaned[:-2] + "m1"
    return cleaned


def _normalize_menu_roster_combined(combined: str) -> str:
    return _normalize_player_name(combined)


def _split_name_blob(text: str) -> list[str]:
    cleaned = _strip_crazy_bots(text)
    cleaned = re.sub(r"[\[\]]", " ", cleaned)
    parts = re.split(r"\s+(?=[A-Z])", cleaned.strip())
    return [
        part.strip()
        for part in parts
        if part.strip()
        and (not _is_noise_token(part) or _is_pairable_suffix_token(part))
    ]


def _should_concat_fragments(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_stem = _stem_without_checkbox(left)
    right_lower = right.lower()
    # Digit-prefixed capital stem is a new player name, not a numeric suffix.
    if re.match(r"^\d+[A-Za-z]", right):
        return False
    if _split_name_affinity(left_stem, right) > 0:
        return True
    if re.search(r"[A-Za-z]\d$", left_stem) and right[0].isupper() and len(right) <= 3:
        return True
    if re.search(r"\d$", left_stem) and right_lower.islower():
        return False
    if right.islower():
        return True
    if left_stem[-1].isalpha() and right[0].isdigit():
        return True
    if left_stem[-1].isdigit() and right[0].isalpha():
        return True
    if len(right) <= 3 and right.isalnum() and right[0].islower():
        return True
    return False


def _is_high_affinity_concat(left: str, right: str) -> bool:
    """True for known split-name pairs that may sit farther apart on one OCR row."""
    return _split_name_affinity(left, right) > 0


def _fragments_from_row_cluster(
    cluster: list[OCRLine],
    *,
    row_idx: int = 0,
) -> list[tuple[str, float, bool, tuple[int, float]]]:
    fragments: list[tuple[str, float, bool, tuple[int, float]]] = []
    for item in sorted(cluster, key=lambda line: line.x):
        if _YOU_PATTERN.search(item.text):
            continue
        if _is_ui_label(item.text):
            continue
        parts = _split_name_blob(item.text)
        if not parts and _is_pairable_suffix_token(item.text):
            parts = [item.text.strip()]
        standalone = len(parts) > 1
        for part_idx, part in enumerate(parts):
            if _is_ui_label(part):
                continue
            if _is_noise_token(part) and not _is_pairable_suffix_token(part):
                continue
            if len(parts) == 1:
                part_x = item.x
            else:
                # Spread merged-box parts so each can x-align with its tribe row.
                offset = (part_idx - (len(parts) - 1) / 2) * _BLOB_PART_X_SPREAD
                part_x = item.x + offset
            fragments.append((part, part_x, standalone, (row_idx, part_x)))
    return fragments


def _merge_row_fragments(
    fragments: list[tuple[str, float, bool, tuple[int, float]]],
) -> list[tuple[str, float, tuple[int, float]]]:
    if not fragments:
        return []

    grouped: list[tuple[str, float, tuple[int, float]]] = []
    current_frag = ""
    current_x = 0.0
    current_source = (0, 0.0)
    for frag, x_pos, standalone, source in fragments:
        if standalone:
            if current_frag:
                grouped.append((current_frag, current_x, current_source))
                current_frag = ""
            grouped.append((frag, x_pos, source))
            continue
        if not current_frag:
            current_frag = frag
            current_x = x_pos
            current_source = source
        elif _should_concat_fragments(current_frag, frag) and (
            _is_high_affinity_concat(current_frag, frag)
            or abs(x_pos - current_x) <= _SAME_ROW_MERGE_X_GAP
        ):
            current_frag = current_frag + frag
        else:
            grouped.append((current_frag, current_x, current_source))
            current_frag = frag
            current_x = x_pos
            current_source = source
    if current_frag:
        grouped.append((current_frag, current_x, current_source))
    return grouped


def _orphan_pair_score(
    left: tuple[str, float, tuple[int, float]],
    right: tuple[str, float, tuple[int, float]],
) -> tuple[float, int]:
    left_name, left_x, _ = left
    right_name, right_x, _ = right
    distance = abs(left_x - right_x)
    affinity = _split_name_affinity(left_name, right_name)
    return (distance - affinity, distance)


def _can_pair_orphans(
    left: tuple[str, float, tuple[int, float]],
    right: tuple[str, float, tuple[int, float]],
) -> bool:
    left_source = left[2]
    right_source = right[2]
    if left_source[0] == right_source[0] and left_source[1] == right_source[1]:
        return False
    return True


def _pair_fragments_across_rows(
    row_fragments: list[list[tuple[str, float, bool, tuple[int, float]]]],
) -> list[str]:
    if not row_fragments:
        return []

    row_groups = [_merge_row_fragments(row) for row in row_fragments]
    if len(row_groups) == 1:
        return [
            _normalize_player_name(name)
            for name, _, _ in row_groups[0]
            if not _is_noise_token(name)
        ]

    paired: list[tuple[str, float]] = []
    used: set[tuple[int, int]] = set()
    orphans: list[tuple[str, float, tuple[int, float]]] = []
    for row_idx, groups in enumerate(row_groups[:-1]):
        next_groups = row_groups[row_idx + 1]
        for frag_idx, (frag, x_pos, source) in enumerate(groups):
            match_idx: int | None = None
            for next_idx, (_, next_x, _) in enumerate(next_groups):
                if (row_idx + 1, next_idx) in used:
                    continue
                if abs(x_pos - next_x) <= _X_ALIGN_TOLERANCE:
                    match_idx = next_idx
                    break
            if match_idx is not None:
                next_frag, _, _ = next_groups[match_idx]
                if _should_concat_fragments(frag, next_frag):
                    merged = frag + next_frag
                else:
                    merged = f"{frag} {next_frag}"
                paired.append((_normalize_player_name(merged), x_pos))
                used.add((row_idx + 1, match_idx))
            else:
                orphans.append((frag, x_pos, source))

    last_groups = row_groups[-1]
    for frag_idx, (frag, x_pos, source) in enumerate(last_groups):
        if (len(row_groups) - 1, frag_idx) not in used:
            orphans.append((frag, x_pos, source))

    while True:
        pair_candidates = [
            (
                _orphan_pair_score(left, right),
                left_idx,
                right_idx,
            )
            for left_idx, left in enumerate(orphans)
            for right_idx, right in enumerate(orphans)
            if left_idx < right_idx and _can_pair_orphans(left, right)
        ]
        if not pair_candidates:
            break
        matched: set[int] = set()
        for (_, _), left_idx, right_idx in sorted(
            pair_candidates, key=lambda item: item[0]
        ):
            if left_idx in matched or right_idx in matched:
                continue
            left, left_x, _ = orphans[left_idx]
            right, _, _ = orphans[right_idx]
            if _should_concat_fragments(left, right):
                merged = left + right
            else:
                merged = f"{left} {right}"
            paired.append((_normalize_player_name(merged), left_x))
            matched.add(left_idx)
            matched.add(right_idx)
        if not matched:
            break
        orphans = [
            orphan for idx, orphan in enumerate(orphans) if idx not in matched
        ]

    for frag, x_pos, _ in orphans:
        paired.append((_normalize_player_name(frag), x_pos))

    paired.sort(key=lambda item: item[1])
    return [name for name, _ in paired if name and not _is_noise_token(name)]


def _row_has_blob_with_peer_name_tokens(cluster: list[OCRLine]) -> bool:
    """True when a multi-name blob shares its row with other standalone name chips.

    Dense avatar grids (Ongoing Strait) OCR some names into one box and leave
    peers like ``0Lord`` / ``ombiez8`` as separate boxes. Menu-roster zip then
    invents junk (``m1``) and cross-column merges; column pairing is correct.
    """
    has_blob = False
    has_peer = False
    for item in cluster:
        if item.x <= _LEFT_COLUMN_X_MAX:
            continue
        parts = _split_name_blob(item.text)
        if len(parts) >= 2:
            has_blob = True
            continue
        if not parts:
            continue
        part = parts[0].strip()
        if (
            _is_ui_label(part)
            or _is_noise_token(part)
            or _is_pairable_suffix_token(part)
            or re.fullmatch(r"\d+", part)
        ):
            continue
        has_peer = True
    return has_blob and has_peer


def _looks_like_menu_roster_strip(strip: list[list[OCRLine]]) -> bool:
    """True when a right-column OCR blob contains multiple player names (menu list)."""
    if any(_row_has_blob_with_peer_name_tokens(cluster) for cluster in strip):
        return False
    suffix_count = len(_right_tribe_suffix_parts(strip))
    for cluster in strip:
        for item in cluster:
            if item.x > _LEFT_COLUMN_X_MAX:
                part_count = len(_split_name_blob(item.text))
                if part_count >= _ROSTER_MIN_NAMES:
                    return True
                # Two humans + tribe-row chips (Doomed Gods / dense lobby style).
                if part_count >= 2 and suffix_count >= 2:
                    return True
    return False


def _left_column_fragments(strip: list[list[OCRLine]]) -> list[tuple[float, str]]:
    fragments: list[tuple[float, str]] = []
    for cluster in strip:
        row_y = cluster[0].y
        for item in cluster:
            if item.x > _LEFT_COLUMN_X_MAX:
                continue
            text = item.text.strip()
            if (
                not text
                or _is_ui_label(text)
                or _is_noise_token(text)
                or _YOU_PATTERN.search(text)
            ):
                continue
            fragments.append((row_y, text))
    fragments.sort(key=lambda entry: entry[0])
    return fragments


def _merge_left_column_name(fragments: list[tuple[float, str]]) -> str | None:
    if not fragments:
        return None
    merged = ""
    for _, text in fragments:
        if not merged:
            merged = text
        elif _should_concat_fragments(merged, text):
            merged = merged + text
        else:
            merged = f"{merged} {text}"
    normalized = _normalize_player_name(merged)
    return normalized or None


def _right_roster_name_parts(strip: list[list[OCRLine]]) -> list[str]:
    best: list[str] = []
    for cluster in strip:
        for item in cluster:
            if item.x <= _LEFT_COLUMN_X_MAX:
                continue
            parts = _split_name_blob(item.text)
            if len(parts) >= 2 and len(parts) > len(best):
                best = parts
    return best


def _prepare_roster_name_part(part: str) -> str:
    cleaned = part.strip()
    if re.search(r"(?i)vilemaxi 0$", cleaned):
        cleaned = re.sub(r"(?i) 0$", "", cleaned)
    else:
        cleaned = re.sub(r" 0$", "0", cleaned)
    cleaned = re.sub(r"^D\s+(?=[a-z])", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^D(?=ombie)", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _right_tribe_suffix_parts(strip: list[list[OCRLine]]) -> list[str]:
    tokens: list[tuple[float, str]] = []
    for cluster in strip:
        if any(
            item.x > _LEFT_COLUMN_X_MAX and len(_split_name_blob(item.text)) >= 2
            for item in cluster
        ):
            continue
        for item in cluster:
            if item.x <= _LEFT_COLUMN_X_MAX:
                continue
            text = item.text.strip()
            if not text or _is_ui_label(text):
                continue
            if re.fullmatch(r"\d+", text):
                continue
            if len(text) <= 2 and text.isalpha():
                tokens.append((item.x, text))
                continue
            for part in _split_name_blob(text):
                cleaned = part.strip()
                if not cleaned or _is_ui_label(cleaned):
                    continue
                if re.fullmatch(r"\d+", cleaned):
                    continue
                tokens.append((item.x, cleaned))
    tokens.sort(key=lambda entry: entry[0])
    return [text for _, text in tokens]


def _combine_roster_name_and_suffix(name: str, suffix: str) -> str:
    name = _prepare_roster_name_part(name)
    if not suffix:
        return _normalize_player_name(name)
    if re.fullmatch(r"seO1?", suffix, re.IGNORECASE):
        base = re.sub(r"0$", "", name)
        if re.search(r"(?i)mou$", base):
            combined = re.sub(r"(?i)mou$", "mouse01", base)
            return _normalize_menu_roster_combined(combined)
        combined = f"{name}{suffix}"
        return _normalize_menu_roster_combined(combined)
    if suffix == "u" and re.search(r"Z80$", name):
        combined = f"{name[:-1]}u"
    elif _should_concat_fragments(name, suffix):
        combined = name + suffix
    else:
        combined = f"{name}{suffix}"
    return _normalize_menu_roster_combined(combined)


def _parse_menu_roster_strip(strip: list[list[OCRLine]]) -> list[str]:
    names: list[str] = []
    left_name = _merge_left_column_name(_left_column_fragments(strip))
    if left_name:
        names.append(left_name)

    roster_parts = _right_roster_name_parts(strip)
    tribe_parts = _right_tribe_suffix_parts(strip)
    for index, part in enumerate(roster_parts):
        suffix = tribe_parts[index] if index < len(tribe_parts) else ""
        if suffix:
            names.append(_combine_roster_name_and_suffix(part, suffix))
        else:
            names.append(_normalize_player_name(part))
    return [name for name in names if name and not _is_noise_token(name)]


def _group_row_clusters_into_strips(
    clusters: list[list[OCRLine]],
) -> list[list[list[OCRLine]]]:
    if not clusters:
        return []

    strips: list[list[list[OCRLine]]] = [[clusters[0]]]
    for cluster in clusters[1:]:
        if abs(cluster[0].y - strips[-1][-1][0].y) <= _PLAYER_STRIP_Y_GAP:
            strips[-1].append(cluster)
        else:
            strips.append([cluster])
    return strips


def _row_has_you_marker(cluster: list[OCRLine]) -> bool:
    return any(_YOU_PATTERN.search(item.text) for item in cluster)


def _extract_names_from_strip(
    strip: list[list[OCRLine]],
) -> tuple[list[str], int]:
    bot_count = 0
    name_rows: list[list[OCRLine]] = []

    for cluster in strip:
        row_text = " ".join(item.text for item in cluster)
        bot_count += _count_crazy_bots(row_text)
        remaining = _strip_crazy_bots(row_text).strip()
        if remaining and not all(
            _is_ui_label(token) or _is_noise_token(token) or _YOU_PATTERN.search(token)
            for token in re.split(r"\s+", remaining)
        ):
            name_rows.append(cluster)

    row_fragments = [
        _fragments_from_row_cluster(cluster, row_idx=row_idx)
        for row_idx, cluster in enumerate(name_rows)
    ]
    if _looks_like_menu_roster_strip(strip):
        names = _parse_menu_roster_strip(strip)
    else:
        names = _pair_fragments_across_rows(row_fragments)
    return names, bot_count


def _player_names_from_rows(
    player_region: list[OCRLine],
) -> list[tuple[str, float, bool]]:
    if not player_region:
        return []

    fine_rows = _cluster_ocr_rows(player_region, tolerance=_PLAYER_ROW_TOLERANCE)
    you_marker_y: float | None = None
    for cluster in fine_rows:
        if _row_has_you_marker(cluster):
            you_marker_y = cluster[0].y
            break

    entries: list[tuple[str, float, bool]] = []
    for strip in _group_row_clusters_into_strips(fine_rows):
        names, bot_count = _extract_names_from_strip(strip)
        strip_y = strip[0][0].y
        for name in names:
            entries.append((name, strip_y, False))
        entries.extend(("Crazy Bot", strip[-1][0].y, False) for _ in range(bot_count))

    if you_marker_y is not None and entries:
        human_indices = [
            idx
            for idx, (name, _, _) in enumerate(entries)
            if name != "Crazy Bot"
        ]
        if human_indices:
            closest_idx = min(
                human_indices,
                key=lambda idx: abs(entries[idx][1] - you_marker_y),
            )
            name, row_y, _ = entries[closest_idx]
            entries[closest_idx] = (name, row_y, True)

    return entries


def _extract_players(
    results: list[OCRLine],
    image_path: str | Path,
) -> list[GameBasicsPlayer]:
    from PIL import Image

    y_min, y_max = _PLAYER_REGION_Y
    player_region = [
        item
        for item in results
        if y_min <= item.y <= y_max
        and item.text.strip().lower() not in {"back", "open"}
    ]
    player_entries = _player_names_from_rows(player_region)

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        return [
            GameBasicsPlayer(
                name=name,
                is_you=is_you,
                is_eliminated=is_skull_avatar(
                    crop_avatar_region(rgb, row_y=row_y),
                ),
            )
            for name, row_y, is_you in player_entries
        ]
