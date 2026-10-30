"""Per-field tests for the Steam-layout parsers, from synthetic OCR output.

No sample image is needed: each test builds the ``OCRLine`` list the
refinement stage would hand the parser, laid out as on a 1280x720 Steam frame.
"""

from __future__ import annotations

import numpy as np
import pytest

from scoretopia.screenshot.parsers import OCRLine
from scoretopia.screenshot.steam import (
    column_runs,
    detect_steam_screenshot_type,
    glyph_count,
    is_steam_frame,
    mask_colored,
    mask_platform_icons,
    parse_steam_game_basics,
    parse_steam_game_end,
    relative_contrast,
)


def _line(text: str, y: float, x: float) -> OCRLine:
    return OCRLine(text=text, confidence=0.9, y=y, x=x)


def _card(**overrides: str | None) -> list[OCRLine]:
    """An Ongoing card: label row at y 295, Glory label at x 604."""
    texts = {
        "header": "Your Turn",
        "title_a": "Test",
        "title_b": "Map One",
        "button": "RESIGN",
        "size": "121",
        "score": "5k",
        "timer": "24",
        "unit": "hours",
        "terrain": "Continents",
        "mode": "Glory",
        "timer_label": "Game Timer",
        "info": "More info",
        "win": "First to reach 5,000 points win",
        "status": "It is your turn to Play",
        "you": "You",
    }
    texts.update(overrides)
    layout = {
        "header": (156, 640),
        "title_a": (209, 505),
        "title_b": (208, 590),
        "button": (209, 766),
        "size": (257, 531),
        "score": (256, 604),
        "timer": (257, 677),
        "unit": (270, 677),
        "terrain": (295, 531),
        "mode": (295, 604),
        "timer_label": (295, 677),
        "info": (295, 750),
        "win": (336, 640),
        "status": (355, 640),
        "you": (381, 588),
    }
    lines = [
        _line(text, *layout[key]) for key, text in texts.items() if text is not None
    ]
    lines += [
        _line("alpha", 435, 614),
        _line("bravo", 436, 676),
        _line("one", 447, 610),
        _line("two", 447, 671),
        _line("BACK", 509, 517),
        _line("OPEN", 509, 761),
    ]
    return lines


def _replay_panel() -> list[OCRLine]:
    """A Replays panel: no timer, Share in the last circle, no You marker."""
    return [
        _line("Last 4 Games", 157, 640),
        _line("Test Map One", 198, 565),
        _line("121", 248, 531),
        _line("25k", 247, 604),
        _line("Pangea", 285, 531),
        _line("Glory", 285, 604),
        _line("More info", 285, 677),
        _line("Share", 285, 750),
        _line("First to reach 25,000 points win", 326, 640),
        _line("This game is over, open it to view the final", 345, 640),
        _line("alpha", 444, 614),
        _line("bravo", 445, 675),
        _line("one", 457, 610),
        _line("two", 457, 671),
        _line("BACK", 517, 517),
        _line("OPEN", 517, 761),
    ]


def _lobby() -> list[OCRLine]:
    """A lobby panel: LEAVE, no win line, an Add slot beside the players."""
    return [
        _line("Test Map One", 217, 590),
        _line("LEAVE", 219, 768),
        _line("121", 266, 531),
        _line("25k", 265, 604),
        _line("7", 265, 677),
        _line("days", 279, 677),
        _line("Pangea", 305, 531),
        _line("Glory", 305, 604),
        _line("Game Timer", 305, 677),
        _line("More info", 305, 750),
        _line("Ready to start!", 344, 640),
        _line("alpha", 426, 583),
        _line("bravo", 426, 643),
        _line("Add", 426, 701),
        _line("one", 438, 580),
        _line("two", 438, 641),
        _line("BACK", 499, 518),
        _line("START GAME", 499, 725),
    ]


# --- game basics: one test per field ------------------------------------


def test_card_game_name_joins_title_tokens_left_of_button() -> None:
    assert parse_steam_game_basics(_card()).game_name == "Test Map One"


def test_card_game_name_drops_trailing_ocr_dot() -> None:
    result = parse_steam_game_basics(_card(title_a="Test."))
    assert result.game_name == "Test Map One"


def test_card_map_size() -> None:
    assert parse_steam_game_basics(_card()).map_size == 121


def test_card_terrain_snaps_to_map_type() -> None:
    assert parse_steam_game_basics(_card()).terrain == "Continents"
    assert parse_steam_game_basics(_card(terrain="Pangae")).terrain == "Pangea"


def test_card_game_type_snaps_misread() -> None:
    assert parse_steam_game_basics(_card()).game_type == "Glory"
    assert parse_steam_game_basics(_card(mode="Glry")).game_type == "Glory"
    assert parse_steam_game_basics(_card(mode="Might")).game_type == "Might"


def test_card_target_score_from_win_line() -> None:
    assert parse_steam_game_basics(_card()).target_score == 5000


def test_card_target_score_from_circle_without_win_line() -> None:
    result = parse_steam_game_basics(_card(win=None, score="Sk"))
    assert result.target_score == 5000
    assert parse_steam_game_basics(_lobby()).target_score == 25000


def test_card_win_condition_text_is_canonical() -> None:
    result = parse_steam_game_basics(_card(win="First to reach 5.000 points win"))
    assert result.win_condition_text == "First to reach 5,000 points win"


def test_lobby_has_no_win_condition_or_turn_status() -> None:
    result = parse_steam_game_basics(_lobby())
    assert result.win_condition_text is None
    assert result.turn_status is None


def test_card_game_timer() -> None:
    assert parse_steam_game_basics(_card()).game_timer == "24 hours"


def test_card_game_timer_unit_picks_option_over_misread_number() -> None:
    result = parse_steam_game_basics(_card(timer="94", unit="bours"))
    assert result.game_timer == "24 hours"
    result = parse_steam_game_basics(_card(timer="71", unit="dy"))
    assert result.game_timer == "7 days"


def test_replay_panel_has_no_timer() -> None:
    assert parse_steam_game_basics(_replay_panel()).game_timer is None


def test_card_turn_status_is_canonical() -> None:
    result = parse_steam_game_basics(_card(status="Itis your turn to Play"))
    assert result.turn_status == "It is your turn to Play"


def test_card_turn_status_waiting() -> None:
    result = parse_steam_game_basics(_card(status="Waiting for bravo to play"))
    assert result.turn_status == "Waiting for bravo to play"


def test_card_players_join_wrapped_name_lines() -> None:
    names = [p.name for p in parse_steam_game_basics(_card()).players]
    assert names == ["alphaone", "bravotwo"]


def test_card_players_strip_leading_icon_residue() -> None:
    lines = [ln for ln in _card() if ln.text != "alpha"]
    lines.append(_line("'alpha", 435, 614))
    names = [p.name for p in parse_steam_game_basics(lines).players]
    assert names[0] == "alphaone"


def test_lobby_players_skip_add_slot() -> None:
    names = [p.name for p in parse_steam_game_basics(_lobby()).players]
    assert names == ["alphaone", "bravotwo"]


def test_card_is_you_marks_column_under_marker() -> None:
    players = parse_steam_game_basics(_card()).players
    assert [p.is_you for p in players] == [True, False]


def test_replay_panel_has_no_you() -> None:
    players = parse_steam_game_basics(_replay_panel()).players
    assert [p.is_you for p in players] == [False, False]


def test_card_is_eliminated_uses_avatar_callback() -> None:
    seen: list[tuple[float, float]] = []

    def is_skull(x: float, y: float) -> bool:
        seen.append((x, y))
        return x > 650

    players = parse_steam_game_basics(_card(), is_skull=is_skull).players
    assert [p.is_eliminated for p in players] == [False, True]
    assert all(y < 435 for _x, y in seen)


def test_card_without_anchor_is_empty() -> None:
    result = parse_steam_game_basics([_line("Hello", 100, 100)])
    assert result.players == ()
    assert result.map_size is None


# --- game end: one test per field ----------------------------------------


def _victory(**overrides: str) -> list[OCRLine]:
    texts = {
        "winner": "alpha wins!",
        "a_detail": "Bardur; score: 515 points",
        "b_detail": "Xin-xi, resigned on turn 0",
        "a_change": "+5",
        "b_change": "-5",
        "stars": "7",
        "turn": "0",
    }
    texts.update(overrides)
    return [
        _line("Score", 19, 585),
        _line("Stars (+2)", 19, 646),
        _line("Turn", 19, 703),
        _line("515", 40, 585),
        _line(texts["stars"], 40, 646),
        _line(texts["turn"], 40, 703),
        _line(texts["winner"], 225, 640),
        _line(texts["a_change"], 262, 787),
        _line("alpha", 284, 555),
        _line("elo", 286, 717),
        _line("1,297", 284, 770),
        _line(texts["a_detail"], 303, 571),
        _line(texts["b_change"], 314, 787),
        _line("bravo", 336, 561),
        _line("elo", 338, 717),
        _line("978", 335, 778),
        _line(texts["b_detail"], 356, 566),
        _line("DONE", 415, 641),
    ]


def test_victory_winner_and_is_winner() -> None:
    result = parse_steam_game_end(_victory())
    assert result.winner == "alpha"
    assert [p.is_winner for p in result.players] == [True, False]


def test_victory_winner_from_misread_line_takes_row_spelling() -> None:
    result = parse_steam_game_end(_victory(winner="alpha] winst"))
    assert result.winner == "alpha"


def test_victory_winner_defaults_to_first_row_without_line() -> None:
    lines = [ln for ln in _victory() if "wins" not in ln.text]
    result = parse_steam_game_end(lines)
    assert result.winner == "alpha"
    assert result.players[0].is_winner


def test_victory_player_names() -> None:
    assert [p.name for p in parse_steam_game_end(_victory()).players] == [
        "alpha",
        "bravo",
    ]


def test_victory_tribes() -> None:
    result = parse_steam_game_end(_victory(a_detail="Ai-Mo, score: 730 pcints"))
    assert [p.tribe for p in result.players] == ["Ai-Mo", "Xin-xi"]


def test_victory_winner_points() -> None:
    assert parse_steam_game_end(_victory()).players[0].score == 515
    result = parse_steam_game_end(_victory(a_detail="Luxidoor; sccre 515 points"))
    assert result.players[0].score == 515
    assert result.players[0].tribe == "Luxidoor"


def test_victory_resigned_turn() -> None:
    result = parse_steam_game_end(_victory())
    assert result.players[1].status == "resigned on turn 0"
    assert result.players[1].score is None


@pytest.mark.parametrize(
    "detail",
    [
        "Xin-xi,resigned on tum um Ohaz k 2",
        "Xin-xi, resigned on Itum Oha 2",
        "Imperius; resigred Jon tumo p 2",
        "Xin-xi, resigned on turn 0",
    ],
)
def test_victory_resigned_turn_survives_ocr_noise(detail: str) -> None:
    result = parse_steam_game_end(_victory(b_detail=detail))
    assert result.players[1].status == "resigned on turn 0"


def test_victory_resigned_turn_two_digits() -> None:
    result = parse_steam_game_end(_victory(b_detail="Xin-xi, resigned on turn 23"))
    assert result.players[1].status == "resigned on turn 23"


def test_victory_elo_and_change() -> None:
    players = parse_steam_game_end(_victory()).players
    assert [p.elo for p in players] == [1297, 978]
    assert [p.elo_change for p in players] == [5, -5]


def test_victory_header() -> None:
    header = parse_steam_game_end(_victory()).header
    assert (header.score, header.stars, header.stars_gained, header.turn) == (
        515,
        7,
        2,
        0,
    )


def test_victory_header_missing_value_is_none() -> None:
    lines = [ln for ln in _victory() if not (ln.y == 40 and ln.x == 703)]
    assert parse_steam_game_end(lines).header.turn is None


# --- detection and frame shape -------------------------------------------


def test_is_steam_frame_landscape_only() -> None:
    assert is_steam_frame((1280, 720))
    assert not is_steam_frame((1080, 2400))


def test_detect_steam_types() -> None:
    assert detect_steam_screenshot_type(_card()) == "game_basics"
    assert detect_steam_screenshot_type(_replay_panel()) == "game_basics"
    assert detect_steam_screenshot_type(_lobby()) == "game_basics"
    assert detect_steam_screenshot_type(_victory()) == "game_end"
    assert detect_steam_screenshot_type([_line("Friends", 10, 10)]) is None


# --- pixel helpers ---------------------------------------------------------


def test_mask_platform_icons_blacks_out_solid_glyphs_only() -> None:
    crop = np.zeros((20, 40, 3), dtype=np.uint8)
    crop[5:15, 2:9] = 255  # solid pure-white icon
    crop[5:15, 20:22] = 200  # anti-aliased grey text stroke
    out = mask_platform_icons(crop)
    assert out[5:15, 2:9].max() == 0
    assert out[5:15, 20:22].min() == 200


def test_mask_platform_icons_green_icon() -> None:
    crop = np.zeros((20, 20, 3), dtype=np.uint8)
    crop[4:14, 4:10] = (30, 186, 33)
    assert mask_platform_icons(crop).max() == 0


def test_mask_colored_keeps_neutral_text() -> None:
    crop = np.zeros((10, 10, 3), dtype=np.uint8)
    crop[:, :5] = (220, 180, 40)  # gold
    crop[:, 5:] = (200, 200, 200)  # grey text
    out = mask_colored(crop)
    assert out[:, :5].max() == 0
    assert out[:, 5:].min() == 200


def test_glyph_count_ignores_single_pixel_noise() -> None:
    gray = np.full((10, 30), 24.0)
    gray[2:8, 3:6] = 50  # glyph
    gray[2:8, 10:13] = 50  # glyph
    gray[5, 20] = 50  # stray pixel
    assert glyph_count(gray) == 2


def test_column_runs_min_pixels_drops_thin_bars() -> None:
    mask = np.zeros((10, 20), dtype=bool)
    mask[5, 0:4] = True  # minus sign: one row thick
    mask[1:9, 6:8] = True  # digit stroke
    assert column_runs(mask, min_pixels=3) == 1
    assert column_runs(mask) == 2


def test_relative_contrast_lifts_dim_text_over_grey() -> None:
    gray = np.full((5, 5), 29.0)
    gray[2, 2] = 45.0
    out = relative_contrast(gray)
    assert out.shape == (5, 5, 3)
    assert out[2, 2, 0] == 255.0
    assert out[0, 0, 0] == 0.0
