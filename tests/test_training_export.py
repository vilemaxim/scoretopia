"""Tests for training artifact export on ingest commit (Task 031).

Config ``training.path`` default/custom loading is covered in
``tests/test_config.py``. This module covers export on successful commit,
metadata shape, failure isolation, README promotion docs, and gitignore.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime
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
from scoretopia.screenshot.models import GameBasicsExtraction, GameBasicsPlayer
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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = PROJECT_ROOT / ".gitignore"
README = PROJECT_ROOT / "README.md"


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
def training_path(tmp_path: Path) -> Path:
    path = tmp_path / "training"
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
    training_path: Path,
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
        training_path=training_path,
        model_dir=MODEL_DIR,
    )


def _bot_mods_config(*discord_user_ids: str, training: Path | None = None) -> Any:
    return SimpleNamespace(
        bot_mods=SimpleNamespace(discord_user_ids=tuple(discord_user_ids)),
        training=SimpleNamespace(path=training or Path("data/training")),
    )


def _game_basics(
    *names: str,
    game_name: str = "Training Export Game",
    is_you_index: int = 0,
) -> GameBasicsExtraction:
    return GameBasicsExtraction(
        game_name=game_name,
        map_size=400,
        terrain="Pangea",
        target_score=20000,
        game_type="Glory",
        game_timer="24 hours",
        players=tuple(
            GameBasicsPlayer(name=name, is_you=(index == is_you_index))
            for index, name in enumerate(names)
        ),
    )


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


def _stage_game_basics(
    ingest_service: IngestService,
    tmp_path: Path,
    *,
    uploader_discord_id: str,
    extraction: GameBasicsExtraction,
    filename: str = "training_export.png",
) -> Any:
    from scoretopia.domain.actions import ExtractionNeedsConfirmation

    source = tmp_path / filename
    Image.new("RGB", (12, 12), color=(50, 80, 50)).save(source)
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


def _confirm_through_final_summary(
    ingest_service: IngestService,
    *,
    parent_interaction_id: int,
    confirmer_discord_id: str,
) -> Any:
    from scoretopia.domain.actions import FinalSummaryNeedsConfirmation
    from scoretopia.domain.player_resolution import (
        mark_all_unresolved_roster_slots_fix_resolved,
    )

    pending = ingest_service._pending_repo.get_by_id(parent_interaction_id)
    assert pending is not None
    mark_all_unresolved_roster_slots_fix_resolved(pending.payload)
    ingest_service._pending_repo.update_payload(
        parent_interaction_id,
        pending.payload,
    )

    paused = ingest_service.continue_review(
        parent_interaction_id,
        confirmer_discord_id=confirmer_discord_id,
    )
    assert isinstance(paused, FinalSummaryNeedsConfirmation)
    return ingest_service.confirm_final_summary(
        paused.interaction_id,
        confirmer_discord_id=confirmer_discord_id,
    )


def _artifact_dir(training_path: Path, interaction_id: int) -> Path:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return training_path / today / str(interaction_id)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_successful_commit_writes_all_five_training_artifacts(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    training_path: Path,
    tmp_path: Path,
) -> None:
    uploader_id = "100000000000000001"
    extraction = _game_basics("TrainAlice", "TrainBob", is_you_index=0)
    _link_humans(player_repo, extraction, uploader_discord_id=uploader_id)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader_id,
        extraction=extraction,
    )

    committed = _confirm_through_final_summary(
        ingest_service,
        parent_interaction_id=staged.interaction_id,
        confirmer_discord_id=uploader_id,
    )
    assert isinstance(committed, GameStarted)

    bundle = _artifact_dir(training_path, staged.interaction_id)
    expected = (
        "screenshot.png",
        "ocr_raw.json",
        "ocr_resolved.json",
        "committed.json",
        "metadata.json",
    )
    for name in expected:
        assert (bundle / name).is_file(), f"missing artifact: {bundle / name}"

    assert (bundle / "screenshot.png").stat().st_size > 0
    for json_name in (
        "ocr_raw.json",
        "ocr_resolved.json",
        "committed.json",
        "metadata.json",
    ):
        assert isinstance(_load_json(bundle / json_name), dict)


def test_ocr_raw_differs_from_committed_when_corrections_applied(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    training_path: Path,
    tmp_path: Path,
) -> None:
    from scoretopia.domain.field_correction import FieldCorrectionService

    mod_id = "111111111111111111"
    config = _bot_mods_config(mod_id, training=training_path)
    correction_service = FieldCorrectionService(pending_repo, config=config)

    extraction = _game_basics(
        "CorrAlice",
        "CorrBob",
        game_name="OCR Typo Name",
        is_you_index=0,
    )
    _link_humans(player_repo, extraction, uploader_discord_id=mod_id)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=mod_id,
        extraction=extraction,
        filename="corrected_export.png",
    )

    correction_service.apply_field_correction(
        parent_interaction_id=staged.interaction_id,
        actor_discord_id=mod_id,
        field="game_name",
        old="OCR Typo Name",
        new="Corrected Game Name",
    )

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    corrected = deserialize_staged_extraction(parent.payload)
    assert isinstance(corrected, GameBasicsExtraction)
    assert corrected.game_name == "Corrected Game Name"

    committed = _confirm_through_final_summary(
        ingest_service,
        parent_interaction_id=staged.interaction_id,
        confirmer_discord_id=mod_id,
    )
    assert isinstance(committed, GameStarted)

    bundle = _artifact_dir(training_path, staged.interaction_id)
    ocr_raw = _load_json(bundle / "ocr_raw.json")
    committed_json = _load_json(bundle / "committed.json")
    assert ocr_raw != committed_json
    assert ocr_raw.get("game_name") == "OCR Typo Name"
    assert committed_json.get("game_name") == "Corrected Game Name"


def test_metadata_includes_correction_count_and_mod_approvals(
    ingest_service: IngestService,
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    training_path: Path,
    tmp_path: Path,
) -> None:
    from scoretopia.domain.field_correction import FieldCorrectionService
    from scoretopia.domain.mod_approval import ModApprovalService

    mod_id = "111111111111111111"
    uploader_id = "999999999999999999"
    config = _bot_mods_config(mod_id, training=training_path)
    correction_service = FieldCorrectionService(pending_repo, config=config)
    approval_service = ModApprovalService(pending_repo, config=config)

    extraction = _game_basics(
        "MetaAlice",
        "MetaBob",
        game_name="Needs Fix",
        is_you_index=0,
    )
    _link_humans(player_repo, extraction, uploader_discord_id=uploader_id)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader_id,
        extraction=extraction,
        filename="metadata_export.png",
    )

    ingest_service.open_fix(
        staged.interaction_id,
        confirmer_discord_id=uploader_id,
    )
    correction_service.apply_field_correction(
        parent_interaction_id=staged.interaction_id,
        actor_discord_id=uploader_id,
        field="game_name",
        old="Needs Fix",
        new="Fixed Name",
    )
    submitted = correction_service.submit_for_approval(
        parent_interaction_id=staged.interaction_id,
        uploader_discord_id=uploader_id,
    )
    approval_service.approve(
        submitted.interaction_id,
        approver_discord_id=mod_id,
    )

    committed = _confirm_through_final_summary(
        ingest_service,
        parent_interaction_id=staged.interaction_id,
        confirmer_discord_id=uploader_id,
    )
    assert isinstance(committed, GameStarted)

    metadata = _load_json(
        _artifact_dir(training_path, staged.interaction_id) / "metadata.json"
    )
    assert metadata["interaction_id"] == staged.interaction_id
    assert metadata["uploader_discord_id"] == uploader_id
    assert metadata["screenshot_type"] == "game_basics"
    assert isinstance(metadata["timestamp"], str)
    assert metadata["timestamp"].endswith("Z") or "+" in metadata["timestamp"]
    assert metadata["correction_count"] >= 1
    assert isinstance(metadata["mod_approvals"], list)
    assert len(metadata["mod_approvals"]) >= 1
    approval = metadata["mod_approvals"][0]
    assert approval["mod_discord_id"] == mod_id
    assert isinstance(approval["approved_at"], str)


def test_export_failure_does_not_roll_back_domain_commit(
    player_repo: PlayerRepo,
    game_repo: GameRepo,
    participant_repo: GameParticipantRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
    dispute_repo: DisputeRepo,
    inbox_path: Path,
    tmp_path: Path,
) -> None:
    """If the training dir cannot be written, domain commit must still succeed."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked", encoding="utf-8")

    ingest_service = IngestService(
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
        training_path=blocked,
        model_dir=MODEL_DIR,
    )

    uploader_id = "100000000000000002"
    extraction = _game_basics(
        "FailAlice",
        "FailBob",
        game_name="Export Failure Game",
        is_you_index=0,
    )
    _link_humans(player_repo, extraction, uploader_discord_id=uploader_id)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader_id,
        extraction=extraction,
        filename="export_fail.png",
    )

    committed = _confirm_through_final_summary(
        ingest_service,
        parent_interaction_id=staged.interaction_id,
        confirmer_discord_id=uploader_id,
    )
    assert isinstance(committed, GameStarted)
    assert committed.game.name == "Export Failure Game"
    assert len(game_repo.list_active()) == 1


def test_gitignore_ignores_data_training_directory() -> None:
    content = GITIGNORE.read_text(encoding="utf-8")
    assert "data/training/" in content or "data/training" in content


def test_git_check_ignore_reports_data_training() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-v", "data/training/example/ocr_raw.json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "Expected data/training/ paths to be ignored by git. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "training" in result.stdout


def test_readme_documents_training_dir_and_promotion_to_samples() -> None:
    readme = README.read_text(encoding="utf-8")
    development = readme.split("## Development", 1)[-1].split("## ", 1)[0].lower()

    assert "training" in development
    assert any(
        term in development
        for term in ("gitignore", "gitignored", "local-only", "not committed")
    )
    assert "samples/screenshots" in development
    assert any(
        term in development for term in ("promote", "promotion", "curated", "copy")
    )
    assert "expected-names-only" in development or "scoretopia-extract" in development
