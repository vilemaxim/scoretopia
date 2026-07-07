"""Tests for in-progress game modal (game-basics) screenshot extraction."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from scoretopia.screenshot.extract import extract_screenshot, format_extraction
from scoretopia.screenshot.game_basics import parse_game_basics
from scoretopia.screenshot.icons import is_skull_avatar
from scoretopia.screenshot.models import GameBasicsExtraction, GameBasicsPlayer
from scoretopia.screenshot.parsers import OCRLine, detect_screenshot_type

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples" / "screenshots"
MODEL_DIR = PROJECT_ROOT / ".easyocr_models"
TODO_PATH = PROJECT_ROOT / "docs" / "tasks" / "TODO.md"

GAME_BASICS_SAMPLES = (
    sorted(SAMPLES_DIR.glob("game-basics*.png")) if SAMPLES_DIR.is_dir() else []
)
LOBBY_SAMPLE = SAMPLES_DIR / "game start error.png"

pytestmark = pytest.mark.skipif(
    not GAME_BASICS_SAMPLES,
    reason="Local game-basics sample screenshots not present",
)


@pytest.fixture(scope="module")
def game_basics_result() -> GameBasicsExtraction:
    result = extract_screenshot(
        GAME_BASICS_SAMPLES[0],
        model_dir=MODEL_DIR,
    )
    assert isinstance(result, GameBasicsExtraction)
    return result


def test_detect_screenshot_type_game_basics_from_sample(
    game_basics_result: GameBasicsExtraction,
) -> None:
    assert game_basics_result.screenshot_type == "game_basics"


def test_game_basics_text_fields(game_basics_result: GameBasicsExtraction) -> None:
    assert game_basics_result.game_name == "Doomed Gods"
    assert game_basics_result.terrain == "Pangea"
    assert game_basics_result.map_size == 400
    assert game_basics_result.game_type == "Glory"
    assert game_basics_result.target_score == 20_000

    timer = game_basics_result.game_timer or ""
    timer_lower = timer.lower()
    assert "24" in timer_lower
    assert "hour" in timer_lower

    win_text = (game_basics_result.win_condition_text or "").lower()
    assert "win" in win_text
    assert "20,000" in win_text or "20000" in win_text

    turn = (game_basics_result.turn_status or "").lower()
    assert "your turn" in turn or "your turn to play" in turn


def test_game_basics_players(game_basics_result: GameBasicsExtraction) -> None:
    names = [player.name for player in game_basics_result.players]
    assert "Lord Union 409" in names
    assert any("vilemaxim" in name.lower() for name in names)
    assert "Diremouse01" in names

    crazy_bots = [
        player for player in game_basics_result.players if player.name == "Crazy Bot"
    ]
    assert len(crazy_bots) >= 2

    you_players = [player for player in game_basics_result.players if player.is_you]
    assert len(you_players) == 1

    assert all(not player.is_eliminated for player in game_basics_result.players)


def test_extract_screenshot_accepts_arbitrary_path(tmp_path: Path) -> None:
    source = GAME_BASICS_SAMPLES[0]
    renamed = tmp_path / "my-random-screenshot-name.png"
    shutil.copy(source, renamed)

    result = extract_screenshot(renamed, model_dir=MODEL_DIR)
    assert isinstance(result, GameBasicsExtraction)
    assert result.game_name == "Doomed Gods"


def test_format_extraction_renders_game_basics_sections(
    game_basics_result: GameBasicsExtraction,
) -> None:
    text = format_extraction(game_basics_result)

    assert "Doomed Gods" in text
    assert "Pangea" in text
    assert "Glory" in text
    assert "Players:" in text or "players" in text.lower()
    assert "400" in text
    assert "20,000" in text or "20000" in text


def test_todo_lists_might_and_bot_type_backlog() -> None:
    content = TODO_PATH.read_text()
    assert "Might" in content
    assert "bot type" in content.lower() or "Bot type" in content


# --- Unit tests (no sample screenshot required) ---


def test_detect_screenshot_type_recognizes_game_basics_modal() -> None:
    ocr_lines = [
        OCRLine(text="Doomed Gods", confidence=0.99, y=100.0, x=200.0),
        OCRLine(text="RESIGN", confidence=0.99, y=120.0, x=400.0),
        OCRLine(text="400", confidence=0.99, y=200.0, x=100.0),
        OCRLine(text="Pangea", confidence=0.99, y=240.0, x=100.0),
        OCRLine(text="20k", confidence=0.99, y=200.0, x=250.0),
        OCRLine(text="Glory", confidence=0.99, y=240.0, x=250.0),
        OCRLine(text="24", confidence=0.99, y=200.0, x=400.0),
        OCRLine(text="hours", confidence=0.99, y=200.0, x=430.0),
        OCRLine(text="Game Timer", confidence=0.99, y=240.0, x=400.0),
        OCRLine(
            text="First to reach 20,000 points win",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
        OCRLine(text="It is your turn to Play", confidence=0.99, y=330.0, x=200.0),
    ]
    assert detect_screenshot_type(ocr_lines) == "game_basics"


def _lobby_ocr_lines() -> list[OCRLine]:
    """Synthetic OCR for pre-game lobby (LEAVE, no RESIGN)."""
    return [
        OCRLine(text="Strait of Uhfixi", confidence=0.99, y=100.0, x=200.0),
        OCRLine(text="LEAVE", confidence=0.99, y=120.0, x=400.0),
        OCRLine(text="324", confidence=0.99, y=200.0, x=100.0),
        OCRLine(text="Pangea", confidence=0.99, y=240.0, x=100.0),
        OCRLine(text="25k", confidence=0.99, y=200.0, x=250.0),
        OCRLine(text="Glory", confidence=0.99, y=240.0, x=250.0),
        OCRLine(text="7", confidence=0.99, y=200.0, x=400.0),
        OCRLine(text="days", confidence=0.99, y=200.0, x=430.0),
        OCRLine(text="Game Timer", confidence=0.99, y=240.0, x=400.0),
        OCRLine(
            text="Waiting for 3 players to accept the invitation",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
    ]


def test_detect_screenshot_type_recognizes_pre_game_lobby_with_leave() -> None:
    assert detect_screenshot_type(_lobby_ocr_lines()) == "game_basics"


def test_detect_screenshot_type_recognizes_lobby_waiting_for_acceptance_text() -> None:
    ocr_lines = [
        OCRLine(text="Strait of Uhfixi", confidence=0.99, y=100.0, x=200.0),
        OCRLine(text="324", confidence=0.99, y=200.0, x=100.0),
        OCRLine(text="Pangea", confidence=0.99, y=240.0, x=100.0),
        OCRLine(text="25k", confidence=0.99, y=200.0, x=250.0),
        OCRLine(text="Glory", confidence=0.99, y=240.0, x=250.0),
        OCRLine(text="Game Timer", confidence=0.99, y=240.0, x=400.0),
        OCRLine(
            text="Waiting for 2 players to accept the invitation",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
    ]
    assert detect_screenshot_type(ocr_lines) == "game_basics"


def test_parse_game_basics_extracts_lobby_fields_with_leave_anchor(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "lobby.png"
    Image.new("RGB", (10, 10)).save(image_path)

    result = parse_game_basics(_lobby_ocr_lines(), image_path)

    assert result.game_name == "Strait of Uhfixi"
    assert result.map_size == 324
    assert result.terrain == "Pangea"
    assert result.game_type == "Glory"
    assert result.target_score == 25_000


@pytest.mark.skipif(
    not LOBBY_SAMPLE.is_file(),
    reason="Local lobby sample screenshot not present",
)
def test_extract_screenshot_recognizes_lobby_sample() -> None:
    result = extract_screenshot(LOBBY_SAMPLE, model_dir=MODEL_DIR)

    assert isinstance(result, GameBasicsExtraction)
    assert result.game_name == "Strait of Uhfixi"
    assert result.map_size == 324
    assert result.terrain == "Pangea"
    assert result.game_type == "Glory"
    assert result.target_score == 25_000


def test_detect_screenshot_type_recognizes_might_without_crashing() -> None:
    ocr_lines = [
        OCRLine(text="War of Kings", confidence=0.99, y=100.0, x=200.0),
        OCRLine(text="RESIGN", confidence=0.99, y=120.0, x=400.0),
        OCRLine(text="Game Timer", confidence=0.99, y=240.0, x=400.0),
        OCRLine(text="Might", confidence=0.99, y=240.0, x=250.0),
    ]
    # Might modal may use placeholder parsing; detection must not raise.
    assert detect_screenshot_type(ocr_lines) == "game_basics"


def test_game_basics_player_dataclass_fields() -> None:
    player = GameBasicsPlayer(name="vilemaxim1", is_you=True, is_eliminated=False)
    assert player.name == "vilemaxim1"
    assert player.is_you is True
    assert player.is_eliminated is False


def test_skull_avatar_detects_grey_skull_patch(tmp_path: Path) -> None:
    img = Image.new("RGB", (48, 48), color=(115, 115, 115))
    path = tmp_path / "skull_patch.png"
    img.save(path)
    assert is_skull_avatar(path) is True


def test_skull_avatar_rejects_colorful_portrait_patch(tmp_path: Path) -> None:
    img = Image.new("RGB", (48, 48))
    pixels = img.load()
    assert pixels is not None
    for x in range(48):
        for y in range(48):
            pixels[x, y] = (x * 5, y * 3, 128)
    path = tmp_path / "portrait_patch.png"
    img.save(path)
    assert is_skull_avatar(path) is False


# --- Row-based player extraction (Task 014) ---


def _minimal_header_lines() -> list[OCRLine]:
    return [
        OCRLine(text="Test Game", confidence=0.99, y=100.0, x=200.0),
        OCRLine(text="RESIGN", confidence=0.99, y=120.0, x=400.0),
        OCRLine(text="Game Timer", confidence=0.99, y=240.0, x=400.0),
    ]


def _save_blank_image(tmp_path: Path, name: str = "players.png") -> Path:
    image_path = tmp_path / name
    Image.new("RGB", (10, 10)).save(image_path)
    return image_path


def test_parse_game_basics_extracts_multi_row_human_grid(tmp_path: Path) -> None:
    """Human names on separate OCR rows are each detected as players."""
    image_path = _save_blank_image(tmp_path)
    lines = [
        *_minimal_header_lines(),
        OCRLine(text="Alice", confidence=0.99, y=1280.0, x=200.0),
        OCRLine(text="Bob", confidence=0.99, y=1360.0, x=200.0),
        OCRLine(text="Charlie", confidence=0.99, y=1440.0, x=200.0),
    ]

    result = parse_game_basics(lines, image_path)

    assert [player.name for player in result.players] == ["Alice", "Bob", "Charlie"]


def test_parse_game_basics_extracts_mixed_humans_and_crazy_bots(
    tmp_path: Path,
) -> None:
    """Rows with human names and Crazy Bot labels yield both player types."""
    image_path = _save_blank_image(tmp_path)
    lines = [
        *_minimal_header_lines(),
        OCRLine(text="Alice", confidence=0.99, y=1280.0, x=200.0),
        OCRLine(text="Crazy Bot", confidence=0.99, y=1360.0, x=200.0),
        OCRLine(text="Bob", confidence=0.99, y=1440.0, x=200.0),
        OCRLine(text="Crazy", confidence=0.99, y=1520.0, x=200.0),
        OCRLine(text="Bot", confidence=0.99, y=1520.0, x=280.0),
    ]

    result = parse_game_basics(lines, image_path)
    names = [player.name for player in result.players]

    assert names.count("Crazy Bot") == 2
    human_names = [name for name in names if name != "Crazy Bot"]
    assert human_names == ["Alice", "Bob"]


def test_parse_game_basics_merges_split_name_tokens_on_same_row(
    tmp_path: Path,
) -> None:
    """Adjacent OCR tokens on one row merge into a single player name."""
    image_path = _save_blank_image(tmp_path)
    lines = [
        *_minimal_header_lines(),
        OCRLine(text="Deoxyrib", confidence=0.99, y=1300.0, x=180.0),
        OCRLine(text="onucleic504", confidence=0.99, y=1300.0, x=320.0),
        OCRLine(text="QombieZ4", confidence=0.99, y=1380.0, x=180.0),
        OCRLine(text="Ru", confidence=0.99, y=1380.0, x=340.0),
    ]

    result = parse_game_basics(lines, image_path)
    names = [player.name for player in result.players]

    assert len(names) == 2
    assert any("deoxyribonucleic" in name.lower() for name in names)
    assert any("zombie" in name.lower() and "4" in name.lower() for name in names)


def test_parse_game_basics_filters_ui_labels_from_player_names(
    tmp_path: Path,
) -> None:
    """UI chrome and noise tokens are excluded from extracted player names."""
    image_path = _save_blank_image(tmp_path)
    lines = [
        *_minimal_header_lines(),
        OCRLine(text="BACK", confidence=0.99, y=1280.0, x=100.0),
        OCRLine(text="OPEN", confidence=0.99, y=1290.0, x=200.0),
        OCRLine(text="START GAME", confidence=0.99, y=1300.0, x=300.0),
        OCRLine(text="Add", confidence=0.99, y=1310.0, x=400.0),
        OCRLine(text="42", confidence=0.99, y=1320.0, x=500.0),
        OCRLine(text="xy", confidence=0.99, y=1330.0, x=600.0),
        OCRLine(text="Zavonics", confidence=0.99, y=1400.0, x=200.0),
    ]

    result = parse_game_basics(lines, image_path)
    names = [player.name for player in result.players]

    assert names == ["Zavonics"]


def test_parse_game_basics_marks_is_you_on_row_with_you_marker(
    tmp_path: Path,
) -> None:
    """The player row adjacent to a You marker is flagged is_you."""
    image_path = _save_blank_image(tmp_path)
    lines = [
        *_minimal_header_lines(),
        OCRLine(text="You", confidence=0.99, y=1280.0, x=100.0),
        OCRLine(text="Alice", confidence=0.99, y=1280.0, x=220.0),
        OCRLine(text="Bob", confidence=0.99, y=1360.0, x=220.0),
    ]

    result = parse_game_basics(lines, image_path)
    players_by_name = {player.name: player for player in result.players}

    assert players_by_name["Alice"].is_you is True
    assert players_by_name["Bob"].is_you is False


_LOBBY_HUMAN_SIGNATURES: tuple[tuple[str, ...], ...] = (
    ("deoxyrib", "onucleic", "nucleic504"),
    ("diremou", "diremouse", "seo1"),
    ("lord", "union"),
    ("vilemaxi",),
    ("zavonic", "dzavonic"),
    ("zombie", "qombie"),
)


def _count_lobby_humans_detected(names: list[str]) -> int:
    joined = " ".join(names).lower()
    return sum(
        1
        for fragments in _LOBBY_HUMAN_SIGNATURES
        if any(fragment in joined for fragment in fragments)
    )


@pytest.mark.skipif(
    not LOBBY_SAMPLE.is_file(),
    reason="Local lobby sample screenshot not present",
)
def test_lobby_sample_extracts_full_human_roster() -> None:
    """Lobby screenshot yields at least five of six humans plus six Crazy Bots."""
    result = extract_screenshot(LOBBY_SAMPLE, model_dir=MODEL_DIR)
    assert isinstance(result, GameBasicsExtraction)

    names = [player.name for player in result.players]
    assert _count_lobby_humans_detected(names) >= 5

    crazy_bots = [player for player in result.players if player.name == "Crazy Bot"]
    assert len(crazy_bots) >= 6
