"""Tests for screenshot ingest orchestrator (Task 007)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from scoretopia.domain.actions import (
    GameEndNeedsConfirmation,
    GameEndNeedsPick,
    GameEndPendingStart,
    GameStarted,
    UnrecognizedScreenshot,
    WinRatioNeedsConfirmation,
)
from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService

from scoretopia.domain.players import PlayerService
from scoretopia.screenshot.models import (
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndPlayer,
    WinRatio,
)
from scoretopia.storage.db import open_database
from scoretopia.storage.models import GameParticipantInput
from scoretopia.storage.repos import (
    GameParticipantRepo,
    GameRepo,
    PendingInteractionRepo,
    PlayerRepo,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples" / "screenshots"
MODEL_DIR = PROJECT_ROOT / ".easyocr_models"

GAME_BASICS_SAMPLE = SAMPLES_DIR / "game-basics.png"
GAME_END_SAMPLE = SAMPLES_DIR / "game_end.png"
FRIEND_PROFILE_SAMPLE = SAMPLES_DIR / "players_compared.png"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def player_repo(conn: sqlite3.Connection) -> PlayerRepo:
    return PlayerRepo(conn)


@pytest.fixture
def game_repo(conn: sqlite3.Connection) -> GameRepo:
    return GameRepo(conn)


@pytest.fixture
def participant_repo(conn: sqlite3.Connection) -> GameParticipantRepo:
    return GameParticipantRepo(conn)


@pytest.fixture
def pending_repo(conn: sqlite3.Connection) -> PendingInteractionRepo:
    return PendingInteractionRepo(conn)


@pytest.fixture
def player_service(player_repo: PlayerRepo) -> PlayerService:
    return PlayerService(player_repo)


@pytest.fixture
def game_service(
    game_repo: GameRepo,
    participant_repo: GameParticipantRepo,
    player_repo: PlayerRepo,
) -> GameService:
    return GameService(game_repo, participant_repo, player_repo)


@pytest.fixture
def inbox_path(tmp_path: Path) -> Path:
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def ingest_service(
    player_service: PlayerService,
    game_service: GameService,
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> IngestService:
    return IngestService(
        player_service=player_service,
        game_service=game_service,
        pending_repo=pending_repo,
        inbox_path=inbox_path,
        model_dir=MODEL_DIR,
    )


def _game_end_extraction(
    *player_names: str,
    winner: str | None = None,
) -> GameEndExtraction:
    winner = winner or player_names[0]
    return GameEndExtraction(
        winner=winner,
        players=tuple(
            GameEndPlayer(name=name, is_winner=(name == winner))
            for name in player_names
        ),
    )


def _create_active_game_with_players(
    game_service: GameService,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
    *,
    game_name: str,
    player_names: tuple[str, ...],
) -> int:
    game = game_service.start_game(
        name=game_name,
        extraction=GameBasicsExtraction(
            game_name=game_name,
            players=tuple(GameBasicsPlayer(name=name) for name in player_names),
        ),
    )
    participants = []
    for name in player_names:
        player = player_repo.get_by_polytopia_name(name)
        assert player is not None
        participants.append(GameParticipantInput(player_id=player.id))
    participant_repo.add_participants(game.id, participants)
    return game.id


# --- Unit tests (mocked extraction) ---


def test_ingest_unrecognized_screenshot_returns_helpful_message(
    ingest_service: IngestService,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "not-polytopia.png"
    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(image_path)

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        side_effect=ValueError("Unrecognized screenshot type"),
    ):
        result = ingest_service.ingest(image_path, uploader_discord_id="uploader-1")

    assert isinstance(result, UnrecognizedScreenshot)
    assert result.message
    assert any(
        hint in result.message.lower()
        for hint in ("game", "screenshot", "polytopia", "recognize")
    )


def test_ingest_game_end_one_match_returns_needs_confirmation(
    ingest_service: IngestService,
    game_service: GameService,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    player_names = ("Diremouse01", "Lord Union 409", "vilemaxim")
    game_id = _create_active_game_with_players(
        game_service,
        player_repo,
        participant_repo,
        game_name="Doomed Gods",
        player_names=player_names,
    )

    image_path = tmp_path / "game_end.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = _game_end_extraction(*player_names)

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        result = ingest_service.ingest(image_path, uploader_discord_id="uploader-2")

    assert isinstance(result, GameEndNeedsConfirmation)
    assert result.game_id == game_id
    assert result.interaction_id > 0

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.discord_user_id == "uploader-2"
    assert pending.status == "open"


def test_ingest_game_end_zero_matches_returns_pending_start(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    assert game_repo.list_active() == []

    image_path = tmp_path / "orphan_game_end.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = _game_end_extraction("Alice", "Bob")

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        result = ingest_service.ingest(image_path, uploader_discord_id="uploader-3")

    assert isinstance(result, GameEndPendingStart)
    assert result.interaction_id > 0

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.discord_user_id == "uploader-3"
    assert pending.status == "open"
    assert game_repo.list_active() == []


def test_ingest_game_end_multiple_matches_returns_needs_pick(
    ingest_service: IngestService,
    game_service: GameService,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
    tmp_path: Path,
) -> None:
    player_names = ("Alice", "Bob")
    game_id_a = _create_active_game_with_players(
        game_service,
        player_repo,
        participant_repo,
        game_name="Game A",
        player_names=player_names,
    )
    game_id_b = _create_active_game_with_players(
        game_service,
        player_repo,
        participant_repo,
        game_name="Game B",
        player_names=player_names,
    )

    image_path = tmp_path / "ambiguous_game_end.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = _game_end_extraction(*player_names)

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        result = ingest_service.ingest(image_path, uploader_discord_id="uploader-4")

    assert isinstance(result, GameEndNeedsPick)
    assert set(result.game_ids) == {game_id_a, game_id_b}
    assert result.interaction_id > 0


def test_ingest_friend_profile_returns_win_ratio_needs_confirmation(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    uploader = player_repo.create(
        polytopia_name="vilemaxim",
        discord_user_id="uploader-5",
        discord_display_name="vile-discord",
    )
    friend = player_repo.create(polytopia_name="Lord Union 409")

    image_path = tmp_path / "friend_profile.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = FriendProfileExtraction(
        friend_name=friend.polytopia_name,
        win_ratio=WinRatio(
            you_name=uploader.polytopia_name,
            you_wins=16,
            friend_name=friend.polytopia_name,
            friend_wins=22,
        ),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        result = ingest_service.ingest(image_path, uploader_discord_id="uploader-5")

    assert isinstance(result, WinRatioNeedsConfirmation)
    assert result.other_player_id == friend.id
    assert result.interaction_id > 0

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.discord_user_id == "uploader-5"


def test_ingest_copies_screenshot_into_inbox(
    ingest_service: IngestService,
    inbox_path: Path,
    tmp_path: Path,
) -> None:
    source = tmp_path / "upload.png"
    Image.new("RGB", (10, 10), color=(0, 255, 0)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Test Game",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
        ),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        ingest_service.ingest(source, uploader_discord_id="uploader-6")

    stored = list(inbox_path.iterdir())
    assert len(stored) == 1
    assert stored[0].is_file()
    assert stored[0].stat().st_size == source.stat().st_size


# --- Integration tests (real OCR on local samples) ---


@pytest.mark.skipif(
    not GAME_BASICS_SAMPLE.is_file(),
    reason="Local game-basics sample screenshot not present",
)
def test_ingest_game_basics_sample_creates_active_game_and_returns_game_started(
    ingest_service: IngestService,
    game_repo: GameRepo,
) -> None:
    result = ingest_service.ingest(
        GAME_BASICS_SAMPLE,
        uploader_discord_id="integration-uploader",
    )

    assert isinstance(result, GameStarted)
    assert result.game.status == "active"
    assert result.game.name == "Doomed Gods"
    assert result.report is not None
    assert result.report.game_id == result.game.id

    active = game_repo.list_active()
    assert len(active) == 1
    assert active[0].id == result.game.id


@pytest.mark.skipif(
    not GAME_END_SAMPLE.is_file(),
    reason="Local game_end sample screenshot not present",
)
def test_ingest_game_end_sample_with_matching_active_game_returns_needs_confirmation(
    ingest_service: IngestService,
    game_service: GameService,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    basics = ingest_service.ingest(
        GAME_BASICS_SAMPLE,
        uploader_discord_id="integration-uploader-2",
    )
    assert isinstance(basics, GameStarted)

    result = ingest_service.ingest(
        GAME_END_SAMPLE,
        uploader_discord_id="integration-uploader-2",
    )

    assert isinstance(result, GameEndNeedsConfirmation)
    assert result.game_id == basics.game.id


@pytest.mark.skipif(
    not GAME_END_SAMPLE.is_file(),
    reason="Local game_end sample screenshot not present",
)
def test_ingest_game_end_sample_without_active_game_returns_pending_start(
    ingest_service: IngestService,
    game_repo: GameRepo,
) -> None:
    assert game_repo.list_active() == []

    result = ingest_service.ingest(
        GAME_END_SAMPLE,
        uploader_discord_id="integration-uploader-3",
    )

    assert isinstance(result, GameEndPendingStart)
    assert game_repo.list_active() == []


@pytest.mark.skipif(
    not FRIEND_PROFILE_SAMPLE.is_file(),
    reason="Local friend profile sample screenshot not present",
)
def test_ingest_friend_profile_sample_returns_win_ratio_needs_confirmation(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
) -> None:
    player_repo.create(
        polytopia_name="vilemaxim",
        discord_user_id="integration-uploader-4",
    )
    player_repo.create(polytopia_name="Lord Union 409")

    result = ingest_service.ingest(
        FRIEND_PROFILE_SAMPLE,
        uploader_discord_id="integration-uploader-4",
    )

    assert isinstance(result, WinRatioNeedsConfirmation)
    friend = player_repo.get_by_polytopia_name("Lord Union 409")
    assert friend is not None
    assert result.other_player_id == friend.id
