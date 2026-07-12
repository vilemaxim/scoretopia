"""Tests for screenshot ingest orchestrator (Task 007)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from scoretopia.domain.actions import (
    ExtractionNeedsConfirmation,
    GameEndNeedsConfirmation,
    GameEndNeedsPick,
    GameEndPendingStart,
    GameStarted,
    StagedIngestNotAuthorized,
    UnrecognizedScreenshot,
    WinRatioNeedsConfirmation,
)
from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService, deserialize_staged_extraction
from scoretopia.domain.matching import is_bot_name
from scoretopia.domain.players import PlayerService
from scoretopia.domain.win_ratios import WinRatioService
from scoretopia.screenshot.models import (
    ExtractionResult,
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndPlayer,
    WinRatio,
)
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

GAME_BASICS_SAMPLE = SAMPLES_DIR / "game-basics.png"
LOBBY_SAMPLE = SAMPLES_DIR / "game start error.png"
GAME_END_SAMPLE = SAMPLES_DIR / "game_end.png"
FRIEND_PROFILE_SAMPLE = SAMPLES_DIR / "players_compared.png"
FIXIOOOIAN_SAMPLE = SAMPLES_DIR / "fixioooian_butte-start.png"

INGEST_LOGGER = "scoretopia.ingest"


def _ingest_log_text(
    caplog: pytest.LogCaptureFixture,
    *,
    level: int = logging.INFO,
) -> str:
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(INGEST_LOGGER) and record.levelno >= level
    )


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
def ratio_repo(conn: sqlite3.Connection) -> PlayerPairRatioRepo:
    return PlayerPairRatioRepo(conn)


@pytest.fixture
def dispute_repo(conn: sqlite3.Connection) -> DisputeRepo:
    return DisputeRepo(conn)


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
def win_ratio_service(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
    dispute_repo: DisputeRepo,
) -> WinRatioService:
    return WinRatioService(
        player_repo,
        pending_repo,
        ratio_repo,
        dispute_repo,
    )


@pytest.fixture
def inbox_path(tmp_path: Path) -> Path:
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def ingest_service(
    player_service: PlayerService,
    game_service: GameService,
    win_ratio_service: WinRatioService,
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> IngestService:
    return IngestService(
        player_service=player_service,
        game_service=game_service,
        win_ratio_service=win_ratio_service,
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
    return game.id


def _stage_screenshot(
    ingest_service: IngestService,
    image_path: Path,
    *,
    uploader_discord_id: str,
) -> ExtractionNeedsConfirmation | UnrecognizedScreenshot:
    stored_path = ingest_service.prepare_stored_path(image_path)
    return ingest_service.stage_screenshot(
        stored_path,
        uploader_discord_id=uploader_discord_id,
    )


def _fix_resolve_unresolved_roster_slots(
    pending_repo: PendingInteractionRepo,
    parent_interaction_id: int,
) -> None:
    """Mark fuzzy/new roster slots Fix-resolved (accept current OCR names).

    Production requires real Fix controls; tests use this to clear the
    continue_review gate without exercising Discord field-correction UI.
    """
    from scoretopia.domain.player_resolution import (
        mark_all_unresolved_roster_slots_fix_resolved,
    )

    pending = pending_repo.get_by_id(parent_interaction_id)
    assert pending is not None
    mark_all_unresolved_roster_slots_fix_resolved(pending.payload)
    pending_repo.update_payload(parent_interaction_id, pending.payload)


def _commit_staged(
    ingest_service: IngestService,
    staged: ExtractionNeedsConfirmation,
    *,
    confirmer_discord_id: str,
):
    from scoretopia.domain.actions import FinalSummaryNeedsConfirmation

    _fix_resolve_unresolved_roster_slots(
        ingest_service._pending_repo,
        staged.interaction_id,
    )
    result = ingest_service.continue_review(
        staged.interaction_id,
        confirmer_discord_id=confirmer_discord_id,
    )
    if isinstance(result, FinalSummaryNeedsConfirmation):
        return ingest_service.confirm_final_summary(
            result.interaction_id,
            confirmer_discord_id=confirmer_discord_id,
        )
    return result


def _link_game_basics_humans(
    player_repo: PlayerRepo,
    extraction: GameBasicsExtraction,
    *,
    uploader_discord_id: str,
) -> None:
    for player in extraction.players:
        if is_bot_name(player.name):
            continue
        discord_id = (
            uploader_discord_id
            if player.is_you
            else f"linked-{player.name.lower().replace(' ', '-')}"
        )
        existing = player_repo.get_by_polytopia_name(player.name)
        if existing is None:
            player_repo.create(
                polytopia_name=player.name,
                discord_user_id=discord_id,
            )
            continue
        if existing.discord_user_id is None:
            player_repo.update_discord_link(
                existing.id,
                discord_user_id=discord_id,
                discord_display_name=None,
            )


def _link_staged_game_basics_humans(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    staged: ExtractionNeedsConfirmation,
    *,
    uploader_discord_id: str,
) -> None:
    pending = pending_repo.get_by_id(staged.interaction_id)
    assert pending is not None
    extraction = deserialize_staged_extraction(pending.payload)
    if isinstance(extraction, GameBasicsExtraction):
        _link_game_basics_humans(
            player_repo,
            extraction,
            uploader_discord_id=uploader_discord_id,
        )


def _ingest_via_stage_commit(
    ingest_service: IngestService,
    image_path: Path,
    *,
    uploader_discord_id: str,
    extraction: ExtractionResult | None = None,
    side_effect: BaseException | None = None,
):
    patch_kwargs: dict[str, object] = {}
    if side_effect is not None:
        patch_kwargs["side_effect"] = side_effect
    elif extraction is not None:
        patch_kwargs["return_value"] = extraction

    with patch("scoretopia.domain.ingest.extract_screenshot", **patch_kwargs):
        staged = _stage_screenshot(
            ingest_service,
            image_path,
            uploader_discord_id=uploader_discord_id,
        )
    if not isinstance(staged, ExtractionNeedsConfirmation):
        return staged
    if extraction is not None and isinstance(extraction, GameBasicsExtraction):
        _link_game_basics_humans(
            ingest_service._player_service.player_repo,
            extraction,
            uploader_discord_id=uploader_discord_id,
        )
    return _commit_staged(
        ingest_service,
        staged,
        confirmer_discord_id=uploader_discord_id,
    )


# --- Unit tests (mocked extraction) ---


def test_ingest_unrecognized_screenshot_returns_helpful_message(
    ingest_service: IngestService,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "not-polytopia.png"
    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(image_path)

    result = _ingest_via_stage_commit(
        ingest_service,
        image_path,
        uploader_discord_id="uploader-1",
        side_effect=ValueError("Unrecognized screenshot type"),
    )

    assert isinstance(result, UnrecognizedScreenshot)
    assert result.message
    assert any(
        hint in result.message.lower()
        for hint in ("game", "screenshot", "polytopia", "recognize")
    )


def test_ingest_game_end_one_match_returns_needs_confirmation(
    ingest_service: IngestService,
    game_service: GameService,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    player_names = ("Diremouse01", "Lord Union 409", "vilemaxim")
    game_id = _create_active_game_with_players(
        game_service,
        game_name="Doomed Gods",
        player_names=player_names,
    )

    image_path = tmp_path / "game_end.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = _game_end_extraction(*player_names)

    result = _ingest_via_stage_commit(
        ingest_service,
        image_path,
        uploader_discord_id="uploader-2",
        extraction=extraction,
    )

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

    result = _ingest_via_stage_commit(
        ingest_service,
        image_path,
        uploader_discord_id="uploader-3",
        extraction=extraction,
    )

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
    tmp_path: Path,
) -> None:
    player_names = ("Alice", "Bob")
    game_id_a = _create_active_game_with_players(
        game_service,
        game_name="Game A",
        player_names=player_names,
    )
    game_id_b = _create_active_game_with_players(
        game_service,
        game_name="Game B",
        player_names=player_names,
    )

    image_path = tmp_path / "ambiguous_game_end.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = _game_end_extraction(*player_names)

    result = _ingest_via_stage_commit(
        ingest_service,
        image_path,
        uploader_discord_id="uploader-4",
        extraction=extraction,
    )

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

    result = _ingest_via_stage_commit(
        ingest_service,
        image_path,
        uploader_discord_id="uploader-5",
        extraction=extraction,
    )

    assert isinstance(result, WinRatioNeedsConfirmation)
    assert result.other_player_id == friend.id
    assert result.interaction_id > 0

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.discord_user_id == "uploader-5"


def test_ingest_succeeds_when_image_already_in_inbox(
    ingest_service: IngestService,
    inbox_path: Path,
) -> None:
    source = inbox_path / "already_there.png"
    Image.new("RGB", (10, 10), color=(255, 0, 0)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Test Game",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
        ),
    )

    result = _ingest_via_stage_commit(
        ingest_service,
        source,
        uploader_discord_id="uploader-6b",
        extraction=extraction,
    )

    assert isinstance(result, GameStarted)
    stored = list(inbox_path.iterdir())
    assert len(stored) == 1
    assert stored[0] == source


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

    _ingest_via_stage_commit(
        ingest_service,
        source,
        uploader_discord_id="uploader-6",
        extraction=extraction,
    )

    stored = list(inbox_path.iterdir())
    assert len(stored) == 1
    assert stored[0].is_file()
    assert stored[0].stat().st_size == source.stat().st_size


def test_ingest_game_basics_splits_human_and_bot_players_in_report(
    ingest_service: IngestService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "start_with_bots.png"
    Image.new("RGB", (10, 10), color=(0, 0, 255)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Bots Included",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
            GameBasicsPlayer(name="Crazy Bot"),
            GameBasicsPlayer(name="Hard Bot"),
        ),
    )

    result = _ingest_via_stage_commit(
        ingest_service,
        source,
        uploader_discord_id="uploader-bots",
        extraction=extraction,
    )

    assert isinstance(result, GameStarted)
    assert result.report.human_player_names == ("Alice", "Bob")
    assert result.report.bot_count == 2


# --- Integration tests (real OCR on local samples) ---


def _integration_stage_commit(
    ingest_service: IngestService,
    image_path: Path,
    *,
    uploader_discord_id: str,
    player_repo: PlayerRepo | None = None,
    pending_repo: PendingInteractionRepo | None = None,
):
    from scoretopia.domain.actions import FinalSummaryNeedsConfirmation

    stored_path = ingest_service.prepare_stored_path(image_path)
    staged = ingest_service.stage_screenshot(
        stored_path,
        uploader_discord_id=uploader_discord_id,
    )
    assert isinstance(staged, ExtractionNeedsConfirmation)
    if player_repo is not None and pending_repo is not None:
        _link_staged_game_basics_humans(
            player_repo,
            pending_repo,
            staged,
            uploader_discord_id=uploader_discord_id,
        )
    _fix_resolve_unresolved_roster_slots(
        ingest_service._pending_repo,
        staged.interaction_id,
    )
    result = ingest_service.continue_review(
        staged.interaction_id,
        confirmer_discord_id=uploader_discord_id,
    )
    if isinstance(result, FinalSummaryNeedsConfirmation):
        return ingest_service.confirm_final_summary(
            result.interaction_id,
            confirmer_discord_id=uploader_discord_id,
        )
    return result


@pytest.mark.skipif(
    not LOBBY_SAMPLE.is_file(),
    reason="Local lobby sample screenshot not present",
)
def test_ingest_lobby_sample_returns_game_started_not_unrecognized(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    result = _integration_stage_commit(
        ingest_service,
        LOBBY_SAMPLE,
        uploader_discord_id="integration-lobby-uploader",
        player_repo=player_repo,
        pending_repo=pending_repo,
    )

    assert not isinstance(result, UnrecognizedScreenshot)
    assert isinstance(result, GameStarted)
    assert result.game.name == "Strait of Uhfixi"


@pytest.mark.skipif(
    not GAME_BASICS_SAMPLE.is_file(),
    reason="Local game-basics sample screenshot not present",
)
def test_ingest_game_basics_sample_creates_active_game_and_returns_game_started(
    ingest_service: IngestService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    result = _integration_stage_commit(
        ingest_service,
        GAME_BASICS_SAMPLE,
        uploader_discord_id="integration-uploader",
        player_repo=player_repo,
        pending_repo=pending_repo,
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
    pending_repo: PendingInteractionRepo,
) -> None:
    basics = _integration_stage_commit(
        ingest_service,
        GAME_BASICS_SAMPLE,
        uploader_discord_id="integration-uploader-2",
        player_repo=player_repo,
        pending_repo=pending_repo,
    )
    assert isinstance(basics, GameStarted)

    result = _integration_stage_commit(
        ingest_service,
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

    result = _integration_stage_commit(
        ingest_service,
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

    result = _integration_stage_commit(
        ingest_service,
        FRIEND_PROFILE_SAMPLE,
        uploader_discord_id="integration-uploader-4",
    )

    assert isinstance(result, WinRatioNeedsConfirmation)
    friend = player_repo.get_by_polytopia_name("Lord Union 409")
    assert friend is not None
    assert result.other_player_id == friend.id


@pytest.mark.skipif(
    not FIXIOOOIAN_SAMPLE.is_file(),
    reason="Local fixioooian_butte-start sample not present",
)
def test_ingest_replay_menu_card_stages_game_basics_not_unrecognized(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
) -> None:
    """Task 026: multiplayer Replays menu modal stages as game_basics preview."""
    stored_path = ingest_service.prepare_stored_path(FIXIOOOIAN_SAMPLE)
    result = ingest_service.stage_screenshot(
        stored_path,
        uploader_discord_id="integration-replay-menu",
    )

    assert not isinstance(result, UnrecognizedScreenshot)
    assert isinstance(result, ExtractionNeedsConfirmation)
    assert result.preview.screenshot_type == "game_basics"
    assert result.preview.game_name

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.kind == "confirm_extraction"
    assert pending.payload["screenshot_type"] == "game_basics"


_FORBIDDEN_USER_FACING_PHRASES = (
    "Ongoing games list is not supported",
    "ongoing list is not supported",
    "ongoing games list is unsupported",
)


def test_no_user_facing_ongoing_list_unsupported_message() -> None:
    """Task 026: ingest/bot copy must not reject Ongoing menu cards."""
    src_root = PROJECT_ROOT / "src" / "scoretopia"
    hits: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for phrase in _FORBIDDEN_USER_FACING_PHRASES:
            if phrase.lower() in source.lower():
                rel = path.relative_to(PROJECT_ROOT)
                hits.append(f"{rel}: {phrase!r}")

    assert not hits, "Forbidden Ongoing rejection copy found:\n" + "\n".join(hits)


# --- Structured ingest logging (Task 015) ---


def test_ingest_game_basics_logs_screenshot_summary_and_participants(
    ingest_service: IngestService,
    inbox_path: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    player_repo: PlayerRepo,
) -> None:
    source = tmp_path / "start_game.png"
    Image.new("RGB", (10, 10), color=(0, 128, 0)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Logged Game",
        map_size=12,
        terrain="Drylands",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
            GameBasicsPlayer(name="Crazy Bot"),
            GameBasicsPlayer(name="Hard Bot"),
        ),
    )
    _link_game_basics_humans(
        player_repo,
        extraction,
        uploader_discord_id="uploader-log-1",
    )

    with caplog.at_level(logging.DEBUG, logger=INGEST_LOGGER):
        with patch(
            "scoretopia.domain.ingest.extract_screenshot",
            return_value=extraction,
        ):
            staged = _stage_screenshot(
                ingest_service,
                source,
                uploader_discord_id="uploader-log-1",
            )
            assert isinstance(staged, ExtractionNeedsConfirmation)
            result = _commit_staged(
                ingest_service,
                staged,
                confirmer_discord_id="uploader-log-1",
            )

    assert isinstance(result, GameStarted)
    info_text = _ingest_log_text(caplog, level=logging.INFO)
    stored_path = inbox_path / "start_game.png"

    assert "uploader-log-1" in info_text
    assert "start_game.png" in info_text
    assert str(stored_path) in info_text
    assert "game_basics" in info_text
    assert "Alice" in info_text
    assert "Bob" in info_text
    assert "2" in info_text  # bot count
    assert "Drylands" not in info_text  # full extraction dict stays off INFO

    debug_text = _ingest_log_text(caplog, level=logging.DEBUG)
    assert "Drylands" in debug_text or "map_size" in debug_text


def test_ingest_game_end_no_match_logs_active_games_and_extracted_names(
    ingest_service: IngestService,
    game_repo: GameRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert game_repo.list_active() == []

    image_path = tmp_path / "orphan_game_end.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = _game_end_extraction("Alice", "Bob")

    with caplog.at_level(logging.INFO, logger=INGEST_LOGGER):
        with patch(
            "scoretopia.domain.ingest.extract_screenshot",
            return_value=extraction,
        ):
            staged = _stage_screenshot(
                ingest_service,
                image_path,
                uploader_discord_id="uploader-log-2",
            )
            assert isinstance(staged, ExtractionNeedsConfirmation)
            result = _commit_staged(
                ingest_service,
                staged,
                confirmer_discord_id="uploader-log-2",
            )

    assert isinstance(result, GameEndPendingStart)
    assert result.extracted_human_names == ("Alice", "Bob")
    info_text = _ingest_log_text(caplog)

    assert "0" in info_text  # active game count
    assert "Alice" in info_text
    assert "Bob" in info_text
    assert "NONE" in info_text
    assert "game_end_pending_start" in info_text
    assert str(result.interaction_id) in info_text

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.kind == "game_end_pending_start"


def test_ingest_unrecognized_screenshot_logs_reason_at_info(
    ingest_service: IngestService,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_path = tmp_path / "not-polytopia.png"
    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(image_path)

    with caplog.at_level(logging.INFO, logger=INGEST_LOGGER):
        result = _ingest_via_stage_commit(
            ingest_service,
            image_path,
            uploader_discord_id="uploader-log-3",
            side_effect=ValueError("Unrecognized screenshot type"),
        )

    assert isinstance(result, UnrecognizedScreenshot)
    info_text = _ingest_log_text(caplog)

    assert "uploader-log-3" in info_text
    assert "not-polytopia.png" in info_text
    assert "unrecognized" in info_text.lower() or "recognize" in info_text.lower()


# --- Staged ingest (Task 016) ---


def test_stage_game_basics_creates_pending_without_active_game(
    ingest_service: IngestService,
    game_repo: GameRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    source = tmp_path / "staged_start.png"
    Image.new("RGB", (10, 10), color=(128, 128, 0)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Staged Game",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
        ),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        result = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="stager-1",
        )

    assert isinstance(result, ExtractionNeedsConfirmation)
    assert result.interaction_id > 0
    assert result.preview.screenshot_type == "game_basics"
    assert game_repo.list_active() == []

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.kind == "confirm_extraction"
    assert pending.discord_user_id == "stager-1"
    assert pending.status == "open"
    assert pending.payload["screenshot_type"] == "game_basics"
    assert pending.payload["uploader_discord_id"] == "stager-1"
    assert "screenshot_path" in pending.payload


def test_commit_staged_game_basics_starts_active_game(
    ingest_service: IngestService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    tmp_path: Path,
) -> None:
    source = tmp_path / "commit_start.png"
    Image.new("RGB", (10, 10), color=(0, 128, 128)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Committed Game",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
        ),
    )
    _link_game_basics_humans(
        player_repo,
        extraction,
        uploader_discord_id="committer-1",
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="committer-1",
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)

    result = _commit_staged(
        ingest_service,
        staged,
        confirmer_discord_id="committer-1",
    )

    assert isinstance(result, GameStarted)
    assert result.game.status == "active"
    assert result.game.name == "Committed Game"
    active = game_repo.list_active()
    assert len(active) == 1
    assert active[0].id == result.game.id


def test_reject_staged_opens_field_correction_without_active_game(
    ingest_service: IngestService,
    game_repo: GameRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    from scoretopia.domain.actions import FieldCorrectionNeedsInput

    source = tmp_path / "reject_start.png"
    Image.new("RGB", (10, 10), color=(128, 0, 128)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Rejected Game",
        players=(
            GameBasicsPlayer(name="Alice"),
            GameBasicsPlayer(name="Bob"),
        ),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="rejecter-1",
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)

    reject_result = ingest_service.open_fix(
        staged.interaction_id,
        confirmer_discord_id="rejecter-1",
    )
    assert isinstance(reject_result, FieldCorrectionNeedsInput)
    assert reject_result.parent_extraction_interaction_id == staged.interaction_id

    assert game_repo.list_active() == []
    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    assert parent.status == "open"
    assert pending_repo.list_open_by_kind("field_correction")


def test_commit_staged_game_end_no_match_includes_roster_diagnostics(
    ingest_service: IngestService,
    game_service: GameService,
    game_repo: GameRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    _create_active_game_with_players(
        game_service,
        game_name="Other Game",
        player_names=("Charlie", "Dave"),
    )
    assert len(game_repo.list_active()) == 1

    image_path = tmp_path / "staged_orphan_end.png"
    Image.new("RGB", (10, 10)).save(image_path)
    extraction = _game_end_extraction("Alice", "Bob")

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            image_path,
            uploader_discord_id="end-stager-1",
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)
    assert len(game_repo.list_active()) == 1

    result = _commit_staged(
        ingest_service,
        staged,
        confirmer_discord_id="end-stager-1",
    )

    assert isinstance(result, GameEndPendingStart)
    assert result.extracted_human_names == ("Alice", "Bob")
    assert len(result.active_game_rosters) == 1
    assert "Other Game" in result.active_game_rosters[0]
    assert "Charlie" in result.active_game_rosters[0]
    assert "Dave" in result.active_game_rosters[0]

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.kind == "game_end_pending_start"


def test_commit_staged_by_other_user_returns_not_authorized(
    ingest_service: IngestService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "unauthorized_commit.png"
    Image.new("RGB", (10, 10)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Protected Game",
        players=(GameBasicsPlayer(name="Alice"), GameBasicsPlayer(name="Bob")),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="owner-1",
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)

    result = _commit_staged(
        ingest_service,
        staged,
        confirmer_discord_id="intruder-1",
    )

    assert isinstance(result, StagedIngestNotAuthorized)


def test_reject_staged_by_other_user_returns_not_authorized(
    ingest_service: IngestService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "unauthorized_reject.png"
    Image.new("RGB", (10, 10)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Protected Game 2",
        players=(GameBasicsPlayer(name="Alice"), GameBasicsPlayer(name="Bob")),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="owner-2",
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)

    result = ingest_service.open_fix(
        staged.interaction_id,
        confirmer_discord_id="intruder-2",
    )

    assert isinstance(result, StagedIngestNotAuthorized)


def test_stage_unrecognized_screenshot_creates_no_pending_row(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "bad_stage.png"
    Image.new("RGB", (10, 10)).save(image_path)

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        side_effect=ValueError("Unrecognized screenshot type"),
    ):
        stored_path = ingest_service.prepare_stored_path(image_path)
        result = ingest_service.stage_screenshot(
            stored_path,
            uploader_discord_id="stager-bad",
        )

    assert isinstance(result, UnrecognizedScreenshot)
    assert pending_repo.list_open_by_kind("confirm_extraction") == []


# --- Player identity verification (Task 018) ---


def _require_player_link_needs_confirmation():
    try:
        from scoretopia.domain.actions import PlayerLinkNeedsConfirmation

        return PlayerLinkNeedsConfirmation
    except ImportError as exc:
        pytest.fail(f"PlayerLinkNeedsConfirmation not implemented: {exc}")


def test_commit_staged_new_human_returns_player_link_needs_confirmation(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    PlayerLinkNeedsConfirmation = _require_player_link_needs_confirmation()
    player_repo.create(
        polytopia_name="LinkedAlice",
        discord_user_id="uploader-identity",
    )
    source = tmp_path / "identity_new_human.png"
    Image.new("RGB", (10, 10), color=(64, 64, 64)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Identity Flow Game",
        players=(
            GameBasicsPlayer(name="LinkedAlice", is_you=True),
            GameBasicsPlayer(name="NewBob"),
        ),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="uploader-identity",
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)

    result = _commit_staged(
        ingest_service,
        staged,
        confirmer_discord_id="uploader-identity",
    )

    assert isinstance(result, PlayerLinkNeedsConfirmation)
    assert result.parent_extraction_interaction_id == staged.interaction_id
    assert len(result.unresolved) == 1
    assert result.unresolved[0].polytopia_name == "NewBob"

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.kind == "confirm_player_link"
    assert pending.payload["parent_extraction_interaction_id"] == staged.interaction_id


def test_identity_confirm_flow_starts_game_with_linked_player(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    try:
        from scoretopia.domain.player_identity import PlayerIdentityService
    except ImportError as exc:
        pytest.fail(f"PlayerIdentityService not implemented: {exc}")

    PlayerLinkNeedsConfirmation = _require_player_link_needs_confirmation()
    player_repo.create(
        polytopia_name="LinkedAlice",
        discord_user_id="uploader-flow",
    )
    source = tmp_path / "identity_flow.png"
    Image.new("RGB", (10, 10), color=(32, 32, 32)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Linked After Confirm",
        players=(
            GameBasicsPlayer(name="LinkedAlice", is_you=True),
            GameBasicsPlayer(name="FlowBob"),
        ),
    )
    identity_service = PlayerIdentityService(player_repo, pending_repo)

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="uploader-flow",
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)

    paused = _commit_staged(
        ingest_service,
        staged,
        confirmer_discord_id="uploader-flow",
    )
    assert isinstance(paused, PlayerLinkNeedsConfirmation)

    identity_service.confirm_spelling(
        paused.interaction_id,
        slot_index=1,
        confirmer_discord_id="uploader-flow",
    )
    identity_service.select_discord_user(
        paused.interaction_id,
        slot_index=1,
        selected_discord_user_id="flow-bob-discord",
        confirmer_discord_id="uploader-flow",
    )
    identity_service.confirm_remote_link(
        paused.interaction_id,
        slot_index=1,
        confirmer_discord_id="flow-bob-discord",
    )

    result = _commit_staged(
        ingest_service,
        staged,
        confirmer_discord_id="uploader-flow",
    )

    assert isinstance(result, GameStarted)
    assert result.game.name == "Linked After Confirm"
    linked = player_repo.get_by_polytopia_name("FlowBob")
    assert linked is not None
    assert linked.discord_user_id == "flow-bob-discord"
    assert len(game_repo.list_active()) == 1


# --- DB-assisted roster resolution staging (Task 028) ---


def _resolved_record(entry: object) -> dict[str, object]:
    if hasattr(entry, "raw_ocr"):
        return {
            "raw_ocr": entry.raw_ocr,
            "suggested_name": entry.suggested_name,
            "confidence": entry.confidence,
            "match_type": entry.match_type,
        }
    assert isinstance(entry, dict)
    return {
        "raw_ocr": entry["raw_ocr"],
        "suggested_name": entry["suggested_name"],
        "confidence": entry["confidence"],
        "match_type": entry["match_type"],
    }


def test_stage_screenshot_stores_raw_extraction_and_resolved_roster(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    tmp_path: Path,
) -> None:
    """Task 028: staged payload keeps raw OCR, resolved roster, working extraction."""
    player_repo.create(polytopia_name="Alice")
    player_repo.create(polytopia_name="Robert")

    source = tmp_path / "roster-resolve.png"
    Image.new("RGB", (10, 10), color=(0, 128, 0)).save(source)
    extraction = GameBasicsExtraction(
        game_name="Roster Resolve Game",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Roberrt"),
            GameBasicsPlayer(name="ZedUnknown"),
            GameBasicsPlayer(name="Crazy Bot"),
        ),
    )

    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = _stage_screenshot(
            ingest_service,
            source,
            uploader_discord_id="resolver-1",
        )

    assert isinstance(staged, ExtractionNeedsConfirmation)
    pending = pending_repo.get_by_id(staged.interaction_id)
    assert pending is not None

    assert "raw_extraction" in pending.payload
    assert "resolved_roster" in pending.payload
    assert "extraction" in pending.payload
    assert "slot_confirmations" in pending.payload

    raw_extraction = pending.payload["raw_extraction"]
    assert isinstance(raw_extraction, dict)
    assert raw_extraction["players"][0]["name"] == "Alice"
    assert raw_extraction["players"][1]["name"] == "Roberrt"
    assert raw_extraction["players"][2]["name"] == "ZedUnknown"

    resolved = pending.payload["resolved_roster"]
    assert isinstance(resolved, list)
    records = [_resolved_record(entry) for entry in resolved]
    by_raw = {str(record["raw_ocr"]): record for record in records}
    assert set(by_raw) == {"Alice", "Roberrt", "ZedUnknown"}
    assert by_raw["Alice"]["match_type"] == "exact"
    assert by_raw["Roberrt"]["match_type"] == "fuzzy"
    assert by_raw["Roberrt"]["suggested_name"] == "Robert"
    assert by_raw["ZedUnknown"]["match_type"] == "new"

    working = pending.payload["extraction"]
    assert isinstance(working, dict)
    working_names = [player["name"] for player in working["players"]]
    # Exact matches applied; fuzzy/new keep raw OCR until slot confirm.
    assert working_names == ["Alice", "Roberrt", "ZedUnknown", "Crazy Bot"]

    slot_confirmations = pending.payload["slot_confirmations"]
    assert isinstance(slot_confirmations, dict)
    # Exact slot auto-confirmed; fuzzy/new require explicit acknowledgement.
    confirmed = {
        int(index): bool(flag) for index, flag in slot_confirmations.items()
    }
    exact_indexes = [
        index
        for index, record in enumerate(records)
        if record["match_type"] == "exact"
    ]
    fuzzy_new_indexes = [
        index
        for index, record in enumerate(records)
        if record["match_type"] in {"fuzzy", "new"}
    ]
    for index in exact_indexes:
        assert confirmed.get(index) is True
    for index in fuzzy_new_indexes:
        assert confirmed.get(index) is not True
