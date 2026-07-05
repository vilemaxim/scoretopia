"""Tests for Polytopia screenshot extraction."""

from pathlib import Path

import pytest

from scoretopia.screenshot.extract import extract_screenshot, format_extraction
from scoretopia.screenshot.models import FriendProfileExtraction, GameEndExtraction

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples" / "screenshots"
MODEL_DIR = PROJECT_ROOT / ".easyocr_models"


pytestmark = pytest.mark.skipif(
    not (SAMPLES_DIR / "game_end.png").is_file(),
    reason="Local sample screenshots not present",
)


@pytest.fixture(scope="module")
def game_end_result() -> GameEndExtraction:
    result = extract_screenshot(
        SAMPLES_DIR / "game_end.png",
        model_dir=MODEL_DIR,
    )
    assert isinstance(result, GameEndExtraction)
    return result


@pytest.fixture(scope="module")
def friend_profile_result() -> FriendProfileExtraction:
    result = extract_screenshot(
        SAMPLES_DIR / "players_compared.png",
        model_dir=MODEL_DIR,
    )
    assert isinstance(result, FriendProfileExtraction)
    return result


def test_game_end_detects_winner(game_end_result: GameEndExtraction) -> None:
    assert game_end_result.winner == "Diremouse01"


def test_game_end_header_stats(game_end_result: GameEndExtraction) -> None:
    assert game_end_result.header.score == 19_040
    assert game_end_result.header.stars == 121
    assert game_end_result.header.stars_gained == 89
    assert game_end_result.header.turn == 31


def test_game_end_players(game_end_result: GameEndExtraction) -> None:
    names = [player.name for player in game_end_result.players]
    assert "Diremouse01" in names
    assert "Lord Union 409" in names
    assert "vilemaxim" in names

    winner_rows = [p for p in game_end_result.players if p.is_winner]
    assert len(winner_rows) == 1
    assert winner_rows[0].name == "Diremouse01"
    assert winner_rows[0].score == 19_040


def test_game_end_player_tribes_resolved(game_end_result: GameEndExtraction) -> None:
    tribes_by_name = {player.name: player.tribe for player in game_end_result.players}
    assert tribes_by_name["Diremouse01"] == "Elyrion"
    assert tribes_by_name["Lord Union 409"] == "Imperius"
    assert tribes_by_name["vilemaxim"] == "Vengir"


def test_friend_profile_fields(friend_profile_result: FriendProfileExtraction) -> None:
    assert friend_profile_result.friend_name == "Lord Union 409"
    assert friend_profile_result.alias == "Lord Union 409"
    assert friend_profile_result.num_friends == 6
    assert friend_profile_result.games_played == 76
    assert friend_profile_result.game_version == 122
    assert friend_profile_result.elo == 1213


def test_friend_profile_win_ratio(
    friend_profile_result: FriendProfileExtraction,
) -> None:
    ratio = friend_profile_result.win_ratio
    assert ratio.you_name == "vilemaxim"
    assert ratio.you_wins == 16
    assert ratio.friend_name == "Lord Union 409"
    assert ratio.friend_wins == 22


def test_format_extraction_writes_expected_sections(
    game_end_result: GameEndExtraction,
    friend_profile_result: FriendProfileExtraction,
) -> None:
    game_text = format_extraction(game_end_result)
    friend_text = format_extraction(friend_profile_result)

    assert "Winner: Diremouse01" in game_text
    assert "Win ratio (head-to-head):" in friend_text
    assert "Lord Union 409: 22" in friend_text
