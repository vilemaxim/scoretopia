"""Tests for field-by-field correction and final summary confirmation (Task 030).

Design choice (documented for implementers):
- Reject opens a separate ``field_correction`` pending that references
  ``parent_extraction_interaction_id``; the parent ``confirm_extraction``
  pending stays open (same pattern as ``mod_approval`` / ``confirm_player_link``).
- Corrections accumulate as ``corrections: [{field, old, new, ...}]``.
- Final domain commit requires ``confirm_final_summary`` for every uploader.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from scoretopia.domain.actions import GameStarted
from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService, deserialize_staged_extraction
from scoretopia.domain.players import PlayerService
from scoretopia.domain.win_ratios import WinRatioService
from scoretopia.screenshot.models import (
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndPlayer,
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

MODEL_DIR = Path(__file__).resolve().parent.parent / ".easyocr_models"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def pending_repo(conn: sqlite3.Connection) -> PendingInteractionRepo:
    return PendingInteractionRepo(conn)


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
def ratio_repo(conn: sqlite3.Connection) -> PlayerPairRatioRepo:
    return PlayerPairRatioRepo(conn)


@pytest.fixture
def dispute_repo(conn: sqlite3.Connection) -> DisputeRepo:
    return DisputeRepo(conn)


@pytest.fixture
def inbox_path(tmp_path: Path) -> Path:
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def ingest_service(
    player_repo: PlayerRepo,
    game_repo: GameRepo,
    participant_repo: GameParticipantRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
    dispute_repo: DisputeRepo,
    inbox_path: Path,
) -> IngestService:
    return IngestService(
        player_service=PlayerService(player_repo),
        game_service=GameService(game_repo, participant_repo, player_repo),
        win_ratio_service=WinRatioService(
            player_repo,
            pending_repo,
            ratio_repo,
            dispute_repo,
        ),
        pending_repo=pending_repo,
        inbox_path=inbox_path,
        model_dir=MODEL_DIR,
    )


def _bot_mods_config(*discord_user_ids: str) -> Any:
    return SimpleNamespace(
        bot_mods=SimpleNamespace(discord_user_ids=tuple(discord_user_ids)),
        training=SimpleNamespace(path=Path("data/training")),
    )


def _require_field_correction_api() -> Any:
    try:
        from scoretopia.domain import field_correction

        return field_correction
    except ImportError as exc:
        pytest.fail(f"field_correction module not implemented: {exc}")


def _require_field_correction_needs_input() -> type:
    try:
        from scoretopia.domain.actions import FieldCorrectionNeedsInput

        return FieldCorrectionNeedsInput
    except ImportError as exc:
        pytest.fail(f"FieldCorrectionNeedsInput not implemented: {exc}")


def _require_final_summary_needs_confirmation() -> type:
    try:
        from scoretopia.domain.actions import FinalSummaryNeedsConfirmation

        return FinalSummaryNeedsConfirmation
    except ImportError as exc:
        pytest.fail(f"FinalSummaryNeedsConfirmation not implemented: {exc}")


def _game_basics(
    *names: str,
    game_name: str = "Field Correction Game",
    is_you_index: int = 0,
    **settings: Any,
) -> GameBasicsExtraction:
    return GameBasicsExtraction(
        game_name=game_name,
        map_size=settings.get("map_size", 400),
        terrain=settings.get("terrain", "Pangea"),
        target_score=settings.get("target_score", 20000),
        game_type=settings.get("game_type", "Glory"),
        game_timer=settings.get("game_timer", "24 hours"),
        players=tuple(
            GameBasicsPlayer(name=name, is_you=(index == is_you_index))
            for index, name in enumerate(names)
        ),
    )


def _game_end(
    *names: str,
    winner: str | None = None,
    scores: tuple[int, ...] | None = None,
) -> GameEndExtraction:
    winner_name = winner or names[0]
    score_values = scores or tuple(1000 * (len(names) - i) for i in range(len(names)))
    return GameEndExtraction(
        winner=winner_name,
        players=tuple(
            GameEndPlayer(
                name=name,
                score=score_values[index] if index < len(score_values) else None,
                is_winner=(name == winner_name),
            )
            for index, name in enumerate(names)
        ),
    )


def _parent_with_staged_extraction(
    pending_repo: PendingInteractionRepo,
    *,
    uploader_discord_id: str,
    extraction: GameBasicsExtraction | GameEndExtraction,
    inbox_path: Path,
    filename: str = "field_correction.png",
) -> int:
    from dataclasses import asdict

    pending = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id=uploader_discord_id,
        payload={
            "screenshot_type": extraction.screenshot_type,
            "screenshot_path": str(inbox_path / filename),
            "uploader_discord_id": uploader_discord_id,
            "extraction": asdict(extraction),
            "raw_extraction": asdict(extraction),
        },
    )
    return pending.id


def _field_correction_service(
    pending_repo: PendingInteractionRepo,
    *,
    config: Any,
) -> Any:
    module = _require_field_correction_api()
    return module.FieldCorrectionService(pending_repo, config=config)


def _stage_game_basics(
    ingest_service: IngestService,
    tmp_path: Path,
    *,
    uploader_discord_id: str,
    extraction: GameBasicsExtraction,
) -> Any:
    from scoretopia.domain.actions import ExtractionNeedsConfirmation

    source = tmp_path / "stage_fc.png"
    Image.new("RGB", (10, 10), color=(40, 40, 40)).save(source)
    with patch(
        "scoretopia.domain.ingest.extract_screenshot",
        return_value=extraction,
    ):
        staged = ingest_service.stage_screenshot(
            ingest_service.prepare_stored_path(source),
            uploader_discord_id=uploader_discord_id,
        )
    assert isinstance(staged, ExtractionNeedsConfirmation)
    return staged


def _link_humans(
    player_repo: PlayerRepo,
    extraction: GameBasicsExtraction,
    *,
    uploader_discord_id: str,
) -> None:
    from scoretopia.domain.matching import is_bot_name

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


def test_reject_extraction_opens_field_correction_pending_not_bare_resolve(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    FieldCorrectionNeedsInput = _require_field_correction_needs_input()
    extraction = _game_basics("Alice", "Bob", is_you_index=0)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id="rejecter-fc-1",
        extraction=extraction,
    )

    result = ingest_service.reject_staged(
        staged.interaction_id,
        confirmer_discord_id="rejecter-fc-1",
    )

    assert isinstance(result, FieldCorrectionNeedsInput)
    assert result.action == "field_correction_needs_input"
    assert result.parent_extraction_interaction_id == staged.interaction_id

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    assert parent.status == "open"
    assert parent.kind == "confirm_extraction"

    open_corrections = pending_repo.list_open_by_kind("field_correction")
    assert len(open_corrections) == 1
    assert open_corrections[0].id == result.interaction_id
    assert (
        open_corrections[0].payload.get("parent_extraction_interaction_id")
        == staged.interaction_id
    )


def test_game_basics_field_correction_updates_staged_extraction_payload(
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    config = _bot_mods_config(mod_id)
    service = _field_correction_service(pending_repo, config=config)
    extraction = _game_basics(
        "Alice",
        "Bob",
        game_name="Wrong Name",
        map_size=100,
        terrain="Drylands",
    )
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=mod_id,
        extraction=extraction,
        inbox_path=inbox_path,
    )

    service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=mod_id,
        field="game_name",
        old="Wrong Name",
        new="Correct Name",
    )
    service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=mod_id,
        field="map_size",
        old=100,
        new=400,
    )
    service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=mod_id,
        field="terrain",
        old="Drylands",
        new="Pangea",
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    staged = deserialize_staged_extraction(parent.payload)
    assert isinstance(staged, GameBasicsExtraction)
    assert staged.game_name == "Correct Name"
    assert staged.map_size == 400
    assert staged.terrain == "Pangea"

    corrections = parent.payload.get("corrections")
    assert isinstance(corrections, list)
    assert len(corrections) >= 3
    fields = {entry["field"] for entry in corrections if isinstance(entry, dict)}
    assert {"game_name", "map_size", "terrain"} <= fields


def test_game_end_winner_and_score_correction_updates_payload(
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    config = _bot_mods_config(mod_id)
    service = _field_correction_service(pending_repo, config=config)
    extraction = _game_end("Alice", "Bob", winner="Alice", scores=(900, 800))
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=mod_id,
        extraction=extraction,
        inbox_path=inbox_path,
        filename="game_end_fc.png",
    )

    service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=mod_id,
        field="winner",
        old="Alice",
        new="Bob",
    )
    service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=mod_id,
        field="score",
        slot_index=1,
        old=800,
        new=1200,
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    staged = deserialize_staged_extraction(parent.payload)
    assert isinstance(staged, GameEndExtraction)
    assert staged.winner == "Bob"
    assert staged.players[1].score == 1200
    assert staged.players[1].is_winner is True
    assert staged.players[0].is_winner is False

    corrections = parent.payload.get("corrections")
    assert isinstance(corrections, list)
    assert any(
        isinstance(entry, dict)
        and entry.get("field") == "winner"
        and entry.get("new") == "Bob"
        for entry in corrections
    )


def test_non_mod_field_correction_batch_requires_mod_approval_before_resume(
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    uploader_id = "999999999999999999"
    config = _bot_mods_config(mod_id)
    service = _field_correction_service(pending_repo, config=config)
    extraction = _game_basics("Uploader", "Bob", game_name="Typo Game", is_you_index=0)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=uploader_id,
        extraction=extraction,
        inbox_path=inbox_path,
    )

    service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=uploader_id,
        field="game_name",
        old="Typo Game",
        new="Fixed Game",
    )
    result = service.submit_for_approval(
        parent_interaction_id=parent_id,
        uploader_discord_id=uploader_id,
    )

    assert result.action == "mod_approval_needs_confirmation"
    open_approvals = pending_repo.list_open_by_kind("mod_approval")
    assert len(open_approvals) == 1
    assert open_approvals[0].id == result.interaction_id

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    staged = deserialize_staged_extraction(parent.payload)
    assert isinstance(staged, GameBasicsExtraction)
    # Parent extraction stays unchanged until mod approves.
    assert staged.game_name == "Typo Game"


def test_mod_field_correction_applies_without_mod_approval_pending(
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    config = _bot_mods_config(mod_id)
    service = _field_correction_service(pending_repo, config=config)
    extraction = _game_basics("ModUser", "Bob", game_name="OCR Typo", is_you_index=0)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=mod_id,
        extraction=extraction,
        inbox_path=inbox_path,
    )

    result = service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=mod_id,
        field="game_name",
        old="OCR Typo",
        new="Clean Name",
    )

    assert result is None or getattr(result, "action", None) != (
        "mod_approval_needs_confirmation"
    )
    assert pending_repo.list_open_by_kind("mod_approval") == []

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    staged = deserialize_staged_extraction(parent.payload)
    assert isinstance(staged, GameBasicsExtraction)
    assert staged.game_name == "Clean Name"


def test_final_summary_confirm_required_for_mod_uploader_before_domain_commit(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    FinalSummaryNeedsConfirmation = _require_final_summary_needs_confirmation()
    mod_id = "111111111111111111"
    extraction = _game_basics(
        "ModAlice",
        "ModBob",
        game_name="Final Summary Game",
        is_you_index=0,
    )
    _link_humans(player_repo, extraction, uploader_discord_id=mod_id)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=mod_id,
        extraction=extraction,
    )

    paused = ingest_service.commit_staged(
        staged.interaction_id,
        confirmer_discord_id=mod_id,
    )

    assert isinstance(paused, FinalSummaryNeedsConfirmation)
    assert paused.action == "final_summary_needs_confirmation"
    assert paused.parent_extraction_interaction_id == staged.interaction_id
    assert paused.summary.game_name == "Final Summary Game"
    assert "ModAlice" in paused.summary.roster
    assert "ModBob" in paused.summary.roster
    assert game_repo.list_active() == []

    final_pending = pending_repo.get_by_id(paused.interaction_id)
    assert final_pending is not None
    assert final_pending.kind == "confirm_final_summary"
    assert final_pending.status == "open"

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    assert parent.status == "open"

    committed = ingest_service.confirm_final_summary(
        paused.interaction_id,
        confirmer_discord_id=mod_id,
    )
    assert isinstance(committed, GameStarted)
    assert committed.game.name == "Final Summary Game"
    assert len(game_repo.list_active()) == 1


def test_integration_reject_correct_game_name_mod_approve_final_summary_commit(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    """reject → correct game_name → mod approve → final summary → commit."""
    FieldCorrectionNeedsInput = _require_field_correction_needs_input()
    FinalSummaryNeedsConfirmation = _require_final_summary_needs_confirmation()
    mod_id = "111111111111111111"
    uploader_id = "999999999999999999"
    config = _bot_mods_config(mod_id)
    service = _field_correction_service(pending_repo, config=config)

    extraction = _game_basics(
        "Uploader",
        "Rival",
        game_name="Bad OCR Name",
        is_you_index=0,
    )
    _link_humans(player_repo, extraction, uploader_discord_id=uploader_id)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader_id,
        extraction=extraction,
    )

    rejected = ingest_service.reject_staged(
        staged.interaction_id,
        confirmer_discord_id=uploader_id,
    )
    assert isinstance(rejected, FieldCorrectionNeedsInput)

    service.apply_field_correction(
        parent_interaction_id=staged.interaction_id,
        actor_discord_id=uploader_id,
        field="game_name",
        old="Bad OCR Name",
        new="Good Game Name",
    )
    submitted = service.submit_for_approval(
        parent_interaction_id=staged.interaction_id,
        uploader_discord_id=uploader_id,
    )
    assert submitted.action == "mod_approval_needs_confirmation"

    from scoretopia.domain.mod_approval import ModApprovalService

    ModApprovalService(pending_repo, config=config).approve(
        submitted.interaction_id,
        approver_discord_id=mod_id,
    )

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    corrected = deserialize_staged_extraction(parent.payload)
    assert isinstance(corrected, GameBasicsExtraction)
    assert corrected.game_name == "Good Game Name"

    paused = ingest_service.commit_staged(
        staged.interaction_id,
        confirmer_discord_id=uploader_id,
    )
    assert isinstance(paused, FinalSummaryNeedsConfirmation)
    assert paused.summary.game_name == "Good Game Name"
    assert game_repo.list_active() == []

    committed = ingest_service.confirm_final_summary(
        paused.interaction_id,
        confirmer_discord_id=uploader_id,
    )
    assert isinstance(committed, GameStarted)
    assert committed.game.name == "Good Game Name"
    assert len(game_repo.list_active()) == 1
