"""Tests for screenshot type detection (Tasks 020, 024)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from scoretopia.domain.actions import ExtractionNeedsConfirmation
from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService
from scoretopia.domain.players import PlayerService
from scoretopia.domain.win_ratios import WinRatioService
from scoretopia.screenshot.extract import extract_screenshot
from scoretopia.screenshot.models import (
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
)
from scoretopia.screenshot.parsers import OCRLine, detect_screenshot_type
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import (
    DisputeRepo,
    GameParticipantRepo,
    GameRepo,
    PendingInteractionRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples" / "screenshots"
MODEL_DIR = PROJECT_ROOT / ".easyocr_models"
GAME_END_SAMPLE = SAMPLES_DIR / "game_end.png"
FIXIOOOIAN_SAMPLE = SAMPLES_DIR / "fixioooian_butte-start.png"


@pytest.fixture
def ingest_service(tmp_path: Path) -> IngestService:
    conn = open_database(":memory:")
    player_repo = PlayerRepo(conn)
    pending_repo = PendingInteractionRepo(conn)
    inbox_path = tmp_path / "inbox"
    inbox_path.mkdir()
    service = IngestService(
        player_service=PlayerService(player_repo),
        game_service=GameService(
            GameRepo(conn),
            GameParticipantRepo(conn),
            player_repo,
        ),
        win_ratio_service=WinRatioService(
            player_repo,
            pending_repo,
            PlayerPairRatioRepo(conn),
            DisputeRepo(conn),
        ),
        pending_repo=pending_repo,
        inbox_path=inbox_path,
        model_dir=MODEL_DIR,
    )
    yield service
    conn.close()


def _epic_wasteland_ongoing_ocr_lines() -> list[OCRLine]:
    """Synthetic OCR matching production Epic Wasteland ongoing list card."""
    return [
        OCRLine(text="MULTIPLAYER", confidence=0.99, y=50.0, x=200.0),
        OCRLine(text="Ongoing", confidence=0.99, y=80.0, x=200.0),
        OCRLine(text="Epic Wasteland", confidence=0.99, y=120.0, x=200.0),
        OCRLine(text="BACK", confidence=0.99, y=120.0, x=50.0),
        OCRLine(text="OPEN", confidence=0.99, y=130.0, x=350.0),
        OCRLine(text="400", confidence=0.99, y=200.0, x=100.0),
        OCRLine(text="Pangea", confidence=0.99, y=240.0, x=100.0),
        OCRLine(text="15k", confidence=0.99, y=200.0, x=250.0),
        OCRLine(text="Glory", confidence=0.99, y=240.0, x=250.0),
        OCRLine(text="7", confidence=0.99, y=200.0, x=400.0),
        OCRLine(text="days", confidence=0.99, y=200.0, x=430.0),
        OCRLine(text="Game Timer", confidence=0.99, y=240.0, x=400.0),
        OCRLine(
            text="First to reach 15,000 points win.",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
        OCRLine(
            text="Waiting for vilemaxim to Play",
            confidence=0.99,
            y=330.0,
            x=200.0,
        ),
    ]


def _finished_replay_menu_ocr_lines() -> list[OCRLine]:
    """Synthetic OCR for finished replay menu card (Share, no RESIGN/Game Timer)."""
    return [
        OCRLine(text="MULTIPLAYER", confidence=0.99, y=50.0, x=200.0),
        OCRLine(text="Replays", confidence=0.99, y=80.0, x=200.0),
        OCRLine(text="Epic Wasteland", confidence=0.99, y=120.0, x=200.0),
        OCRLine(text="BACK", confidence=0.99, y=120.0, x=50.0),
        OCRLine(text="OPEN", confidence=0.99, y=130.0, x=350.0),
        OCRLine(text="400", confidence=0.99, y=200.0, x=100.0),
        OCRLine(text="Pangea", confidence=0.99, y=240.0, x=100.0),
        OCRLine(text="15k", confidence=0.99, y=200.0, x=250.0),
        OCRLine(text="Glory", confidence=0.99, y=240.0, x=250.0),
        OCRLine(text="Share", confidence=0.99, y=240.0, x=400.0),
        OCRLine(
            text="First to reach 15,000 points win.",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
        OCRLine(
            text="This game is over.",
            confidence=0.99,
            y=330.0,
            x=200.0,
        ),
    ]


def _finished_match_ocr_lines() -> list[OCRLine]:
    """Synthetic OCR for a completed match (winner announcement)."""
    return [
        OCRLine(text="Diremouse01 wins!", confidence=0.99, y=100.0, x=200.0),
        OCRLine(text="Score: 19,040 points", confidence=0.99, y=200.0, x=200.0),
        OCRLine(
            text="Imperius; Score: 12,000 points",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
        OCRLine(text="Lord Union 409", confidence=0.99, y=280.0, x=100.0),
    ]


def _resigned_match_ocr_lines() -> list[OCRLine]:
    """Synthetic OCR for a match ended by resignation (no winner line)."""
    return [
        OCRLine(text="Match over", confidence=0.99, y=100.0, x=200.0),
        OCRLine(
            text="Vengir; resigned on turn 31",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
        OCRLine(text="vilemaxim", confidence=0.99, y=280.0, x=100.0),
    ]


def test_detect_screenshot_type_ongoing_menu_card_returns_game_basics() -> None:
    """Epic Wasteland Ongoing menu modal should classify as game_basics."""
    assert detect_screenshot_type(_epic_wasteland_ongoing_ocr_lines()) == "game_basics"


def test_detect_screenshot_type_finished_replay_menu_card_returns_game_basics() -> (
    None
):
    """Finished replay card (Share, game is over) classifies as game_basics."""
    assert detect_screenshot_type(_finished_replay_menu_ocr_lines()) == "game_basics"


def test_detect_screenshot_type_game_end_winner_line() -> None:
    assert detect_screenshot_type(_finished_match_ocr_lines()) == "game_end"


def test_detect_screenshot_type_game_end_resigned_on_turn() -> None:
    assert detect_screenshot_type(_resigned_match_ocr_lines()) == "game_end"


def test_detect_screenshot_type_win_in_condition_alone_not_game_end() -> None:
    """Win-condition prose must not trigger game_end without finished-match cues."""
    ocr_lines = [
        OCRLine(text="Doomed Gods", confidence=0.99, y=100.0, x=200.0),
        OCRLine(text="RESIGN", confidence=0.99, y=120.0, x=400.0),
        OCRLine(text="Game Timer", confidence=0.99, y=240.0, x=400.0),
        OCRLine(text="Glory", confidence=0.99, y=240.0, x=250.0),
        OCRLine(
            text="First to reach 20,000 points win",
            confidence=0.99,
            y=300.0,
            x=200.0,
        ),
        OCRLine(text="It is your turn to Play", confidence=0.99, y=330.0, x=200.0),
    ]
    assert detect_screenshot_type(ocr_lines) == "game_basics"


@pytest.mark.skipif(
    not GAME_END_SAMPLE.is_file(),
    reason="Local game_end sample screenshot not present",
)
def test_detect_screenshot_type_real_game_end_sample() -> None:
    """Regression: real game-end screenshot still classifies as game_end."""
    result = extract_screenshot(GAME_END_SAMPLE, model_dir=MODEL_DIR)
    assert isinstance(result, GameEndExtraction)
    assert result.screenshot_type == "game_end"


@pytest.mark.skipif(
    not FIXIOOOIAN_SAMPLE.is_file(),
    reason="Local fixioooian_butte-start sample not present",
)
def test_extract_screenshot_fixioooian_butte_start_recognizes_game_basics() -> None:
    """Regression: multiplayer menu modal must not raise unrecognized type."""
    result = extract_screenshot(FIXIOOOIAN_SAMPLE, model_dir=MODEL_DIR)
    assert result.screenshot_type == "game_basics"


def test_ingest_ongoing_menu_card_stages_game_basics(
    ingest_service: IngestService,
    tmp_path: Path,
) -> None:
    """Multiplayer Ongoing menu card should stage as game_basics, not reject."""
    image_path = tmp_path / "ongoing_card.png"
    Image.new("RGB", (100, 100), color=(0, 128, 0)).save(image_path)
    extraction = GameBasicsExtraction(
        game_name="Epic Wasteland",
        players=(GameBasicsPlayer(name="Alice", is_you=True),),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        stored_path = ingest_service.prepare_stored_path(image_path)
        result = ingest_service.stage_screenshot(
            stored_path,
            uploader_discord_id="uploader-1",
            filename=image_path.name,
        )

    assert isinstance(result, ExtractionNeedsConfirmation)
    assert result.preview.screenshot_type == "game_basics"
