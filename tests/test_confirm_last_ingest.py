"""Tests for confirm-last ingest pipeline (Task 033 / ADR 005).

Domain API choice (document for implementers):
- Keep pending kind ``confirm_extraction`` for the staged diagnosis parent
  (avoids a DB migration); semantics are review + Continue/Fix/Abandon, not
  an extraction-level Confirm commit.
- ``IngestService.continue_review`` replaces ``commit_staged`` as the advance
  from diagnosis → identity (if needed) → final summary.
- ``IngestService.open_fix`` replaces ``reject_staged`` (same field_correction
  pending pattern).
- ``IngestService.abandon_staged`` discards open staged / correction /
  final-summary (and related child) pendings for that ingest with no domain
  mutations.
- ``commit_staged`` must not advance to identity or final summary (removed or
  hard-stopped with a clear non-commit error).
- Fuzzy/new roster slots are resolved only via the Fix path (field correction
  of a player name, or accepting a fuzzy suggestion through Fix). Merely
  flipping ``slot_confirmations`` is not enough for ``continue_review``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from scoretopia.domain.actions import (
    ExtractionNeedsConfirmation,
    FinalSummaryNeedsConfirmation,
    GameStarted,
    IngestError,
    StagedIngestNotAuthorized,
)
from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService, deserialize_staged_extraction
from scoretopia.domain.matching import is_bot_name
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


def _link_humans(
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


def _stage_game_basics(
    ingest_service: IngestService,
    tmp_path: Path,
    *,
    uploader_discord_id: str,
    extraction: GameBasicsExtraction,
    filename: str = "confirm_last.png",
) -> ExtractionNeedsConfirmation:
    source = tmp_path / filename
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


def _exact_linked_extraction(
    player_repo: PlayerRepo,
    *,
    uploader_discord_id: str,
    game_name: str = "Exact Roster Game",
) -> GameBasicsExtraction:
    extraction = GameBasicsExtraction(
        game_name=game_name,
        map_size=400,
        terrain="Pangea",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
        ),
    )
    _link_humans(player_repo, extraction, uploader_discord_id=uploader_discord_id)
    return extraction


def _continue_review(
    ingest_service: IngestService,
    interaction_id: int,
    *,
    confirmer_discord_id: str,
):
    """Call the new continue_review entry point (must exist for Task 033)."""
    assert hasattr(ingest_service, "continue_review"), (
        "IngestService.continue_review is required (ADR 005 / Task 033)"
    )
    return ingest_service.continue_review(
        interaction_id,
        confirmer_discord_id=confirmer_discord_id,
    )


def _open_fix(
    ingest_service: IngestService,
    interaction_id: int,
    *,
    confirmer_discord_id: str,
):
    assert hasattr(ingest_service, "open_fix"), (
        "IngestService.open_fix is required (ADR 005 / Task 033)"
    )
    return ingest_service.open_fix(
        interaction_id,
        confirmer_discord_id=confirmer_discord_id,
    )


def _abandon_staged(
    ingest_service: IngestService,
    interaction_id: int,
    *,
    confirmer_discord_id: str,
):
    assert hasattr(ingest_service, "abandon_staged"), (
        "IngestService.abandon_staged is required (ADR 005 / Task 033)"
    )
    return ingest_service.abandon_staged(
        interaction_id,
        confirmer_discord_id=confirmer_discord_id,
    )


def _field_correction_service(pending_repo: PendingInteractionRepo, *, config: Any):
    from scoretopia.domain.field_correction import FieldCorrectionService

    return FieldCorrectionService(pending_repo, config=config)


def _resolve_fuzzy_or_new_slot_via_fix(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
    *,
    actor_discord_id: str,
    slot_index: int,
    old_name: str,
    new_name: str,
    config: Any,
) -> None:
    """Fix-path roster slot resolution: field-correct the player name.

    Implementers may also mark ``slot_confirmations`` / resolved_roster as part
    of this path; ack-only updates without Fix must not unlock Continue.
    """
    service = _field_correction_service(pending_repo, config=config)
    service.apply_field_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=actor_discord_id,
        field="players",
        old=old_name,
        new=new_name,
        slot_index=slot_index,
    )


def _assert_continue_rejected(result: object) -> None:
    """continue_review must not advance when fuzzy/new slots are unresolved."""
    assert not isinstance(
        result,
        (FinalSummaryNeedsConfirmation, GameStarted),
    ), f"continue_review advanced unexpectedly: {result!r}"
    from scoretopia.domain.actions import PlayerLinkNeedsConfirmation

    assert not isinstance(result, PlayerLinkNeedsConfirmation), (
        "unresolved roster slots must not start identity flow"
    )
    action = getattr(result, "action", None)
    assert action in {
        "roster_slots_unresolved",
        "continue_review_rejected",
        "error",
        "not_authorized",
    } or isinstance(result, (IngestError, StagedIngestNotAuthorized)), (
        f"expected clear continue rejection, got {result!r}"
    )


def test_happy_path_exact_roster_continue_then_final_summary_confirm(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    game_repo: GameRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    """stage → continue → final summary → confirm → game started; no mid commit."""
    uploader = "uploader-continue-exact"
    extraction = _exact_linked_extraction(player_repo, uploader_discord_id=uploader)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader,
        extraction=extraction,
    )
    assert game_repo.list_active() == []

    continued = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(continued, FinalSummaryNeedsConfirmation)
    assert continued.parent_extraction_interaction_id == staged.interaction_id
    assert continued.summary.game_name == "Exact Roster Game"
    assert game_repo.list_active() == []

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    assert parent.status == "open"

    committed = ingest_service.confirm_final_summary(
        continued.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(committed, GameStarted)
    assert committed.game.name == "Exact Roster Game"
    assert len(game_repo.list_active()) == 1


def test_continue_review_rejects_unresolved_fuzzy_and_new_slots(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    player_repo.create(
        polytopia_name="Alice",
        discord_user_id="uploader-fuzzy-gate",
    )
    player_repo.create(polytopia_name="Robert")
    extraction = GameBasicsExtraction(
        game_name="Fuzzy Gate Game",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Roberrt"),
            GameBasicsPlayer(name="ZedUnknown"),
        ),
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id="uploader-fuzzy-gate",
        extraction=extraction,
        filename="fuzzy_gate.png",
    )

    pending = pending_repo.get_by_id(staged.interaction_id)
    assert pending is not None
    slot_confirmations = pending.payload.get("slot_confirmations")
    assert isinstance(slot_confirmations, dict)
    # Ack-only: force all slots "confirmed" without Fix — Continue must still reject.
    pending.payload["slot_confirmations"] = {
        str(index): True for index in slot_confirmations
    }
    pending_repo.update_payload(staged.interaction_id, pending.payload)

    rejected = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id="uploader-fuzzy-gate",
    )
    _assert_continue_rejected(rejected)


def test_fix_resolves_fuzzy_slot_then_continue_succeeds(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    mod_id = "111111111111111111"
    player_repo.create(polytopia_name="Alice", discord_user_id=mod_id)
    player_repo.create(polytopia_name="Robert", discord_user_id="linked-robert")
    extraction = GameBasicsExtraction(
        game_name="Fix Then Continue",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Roberrt"),
        ),
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=mod_id,
        extraction=extraction,
        filename="fix_fuzzy.png",
    )

    rejected = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=mod_id,
    )
    _assert_continue_rejected(rejected)

    from scoretopia.domain.actions import FieldCorrectionNeedsInput

    fix = _open_fix(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=mod_id,
    )
    assert isinstance(fix, FieldCorrectionNeedsInput)
    assert fix.parent_extraction_interaction_id == staged.interaction_id

    # Human roster index 1 is Roberrt (fuzzy → Robert). Full extraction slot_index
    # matches players list index (no bots in this fixture).
    _resolve_fuzzy_or_new_slot_via_fix(
        pending_repo,
        staged.interaction_id,
        actor_discord_id=mod_id,
        slot_index=1,
        old_name="Roberrt",
        new_name="Robert",
        config=_bot_mods_config(mod_id),
    )

    continued = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=mod_id,
    )
    assert isinstance(continued, FinalSummaryNeedsConfirmation)
    assert "Robert" in continued.summary.roster
    assert "Roberrt" not in continued.summary.roster


def test_open_fix_field_override_reflected_in_final_summary(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    from scoretopia.domain.actions import FieldCorrectionNeedsInput

    mod_id = "111111111111111111"
    extraction = _exact_linked_extraction(
        player_repo,
        uploader_discord_id=mod_id,
        game_name="Wrong OCR Name",
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=mod_id,
        extraction=extraction,
        filename="fix_override.png",
    )

    fix = _open_fix(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=mod_id,
    )
    assert isinstance(fix, FieldCorrectionNeedsInput)

    service = _field_correction_service(
        pending_repo,
        config=_bot_mods_config(mod_id),
    )
    service.apply_field_correction(
        parent_interaction_id=staged.interaction_id,
        actor_discord_id=mod_id,
        field="game_name",
        old="Wrong OCR Name",
        new="Corrected Game Name",
    )

    continued = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=mod_id,
    )
    assert isinstance(continued, FinalSummaryNeedsConfirmation)
    assert continued.summary.game_name == "Corrected Game Name"
    assert game_repo.list_active() == []

    committed = ingest_service.confirm_final_summary(
        continued.interaction_id,
        confirmer_discord_id=mod_id,
    )
    assert isinstance(committed, GameStarted)
    assert committed.game.name == "Corrected Game Name"


def test_abandon_staged_clears_pendings_without_domain_rows(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    from scoretopia.domain.actions import FieldCorrectionNeedsInput

    uploader = "uploader-abandon"
    players_before = {p.polytopia_name for p in player_repo.list_all()}
    extraction = GameBasicsExtraction(
        game_name="Abandon Me",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
        ),
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader,
        extraction=extraction,
        filename="abandon.png",
    )

    fix = _open_fix(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(fix, FieldCorrectionNeedsInput)

    _abandon_staged(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    assert parent.status != "open"

    assert pending_repo.list_open_by_kind("confirm_extraction") == []
    assert pending_repo.list_open_by_kind("field_correction") == []
    assert pending_repo.list_open_by_kind("confirm_final_summary") == []
    assert game_repo.list_active() == []
    players_after = {p.polytopia_name for p in player_repo.list_all()}
    assert players_after == players_before


def test_abandon_from_final_summary_also_clears_parent(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    uploader = "uploader-abandon-final"
    extraction = _exact_linked_extraction(player_repo, uploader_discord_id=uploader)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader,
        extraction=extraction,
        filename="abandon_final.png",
    )
    continued = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(continued, FinalSummaryNeedsConfirmation)

    _abandon_staged(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    assert parent.status != "open"
    final = pending_repo.get_by_id(continued.interaction_id)
    assert final is not None
    assert final.status != "open"
    assert game_repo.list_active() == []


def test_commit_staged_no_longer_advances_to_identity_or_final_summary(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    """commit_staged must not be a commit or identity gate (ADR 005)."""
    from scoretopia.domain.actions import PlayerLinkNeedsConfirmation

    uploader = "uploader-old-commit"
    extraction = _exact_linked_extraction(player_repo, uploader_discord_id=uploader)
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader,
        extraction=extraction,
        filename="old_commit.png",
    )

    if not hasattr(ingest_service, "commit_staged"):
        return  # removed — acceptable

    result = ingest_service.commit_staged(
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert not isinstance(
        result,
        (
            FinalSummaryNeedsConfirmation,
            PlayerLinkNeedsConfirmation,
            GameStarted,
        ),
    ), (
        "commit_staged must not advance to identity, final summary, or commit "
        f"(got {type(result).__name__})"
    )
    assert game_repo.list_active() == []
    action = getattr(result, "action", None)
    assert action in {
        "not_authorized",
        "error",
        "commit_staged_removed",
        "use_continue_review",
    } or isinstance(result, (IngestError, StagedIngestNotAuthorized)), (
        f"expected clear non-commit error from commit_staged, got {result!r}"
    )


def test_confirm_final_summary_is_sole_domain_commit_entry(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    uploader = "uploader-sole-commit"
    extraction = _exact_linked_extraction(
        player_repo,
        uploader_discord_id=uploader,
        game_name="Sole Commit Game",
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader,
        extraction=extraction,
        filename="sole_commit.png",
    )

    continued = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(continued, FinalSummaryNeedsConfirmation)
    assert game_repo.list_active() == []

    committed = ingest_service.confirm_final_summary(
        continued.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(committed, GameStarted)
    assert len(game_repo.list_active()) == 1


def test_identity_subflow_runs_from_continue_before_final_confirm(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    game_repo: GameRepo,
    tmp_path: Path,
) -> None:
    from scoretopia.domain.actions import PlayerLinkNeedsConfirmation
    from scoretopia.domain.player_identity import PlayerIdentityService

    uploader = "uploader-identity-continue"
    player_repo.create(polytopia_name="LinkedAlice", discord_user_id=uploader)
    extraction = GameBasicsExtraction(
        game_name="Identity Before Confirm",
        players=(
            GameBasicsPlayer(name="LinkedAlice", is_you=True),
            GameBasicsPlayer(name="NewBob"),
        ),
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader,
        extraction=extraction,
        filename="identity_continue.png",
    )

    # NEW roster slot must be Fix-resolved before Continue (ADR 005).
    from scoretopia.domain.actions import FieldCorrectionNeedsInput

    fix = _open_fix(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(fix, FieldCorrectionNeedsInput)
    _resolve_fuzzy_or_new_slot_via_fix(
        pending_repo,
        staged.interaction_id,
        actor_discord_id=uploader,
        slot_index=1,
        old_name="NewBob",
        new_name="NewBob",
        config=_bot_mods_config(uploader),
    )

    paused = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(paused, PlayerLinkNeedsConfirmation)
    assert paused.parent_extraction_interaction_id == staged.interaction_id
    assert game_repo.list_active() == []

    identity = PlayerIdentityService(player_repo, pending_repo)
    identity.confirm_spelling(
        paused.interaction_id,
        slot_index=1,
        confirmer_discord_id=uploader,
    )
    identity.select_discord_user(
        paused.interaction_id,
        slot_index=1,
        selected_discord_user_id="new-bob-discord",
        confirmer_discord_id=uploader,
    )
    identity.confirm_remote_link(
        paused.interaction_id,
        slot_index=1,
        confirmer_discord_id="new-bob-discord",
    )

    continued = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(continued, FinalSummaryNeedsConfirmation)

    committed = ingest_service.confirm_final_summary(
        continued.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(committed, GameStarted)
    linked = player_repo.get_by_polytopia_name("NewBob")
    assert linked is not None
    assert linked.discord_user_id == "new-bob-discord"


def test_identity_cannot_run_after_final_confirm_as_oops_fix(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    """No post-Confirm spelling/identity/Fix path for the uploader (ADR 005)."""
    from scoretopia.domain.player_identity import PlayerIdentityService

    uploader = "uploader-no-post-confirm"
    extraction = _exact_linked_extraction(
        player_repo,
        uploader_discord_id=uploader,
        game_name="No Post Confirm Fix",
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id=uploader,
        extraction=extraction,
        filename="no_post_confirm.png",
    )
    continued = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(continued, FinalSummaryNeedsConfirmation)
    committed = ingest_service.confirm_final_summary(
        continued.interaction_id,
        confirmer_discord_id=uploader,
    )
    assert isinstance(committed, GameStarted)

    parent = pending_repo.get_by_id(staged.interaction_id)
    assert parent is not None
    assert parent.status != "open"

    # Continue / Fix must not reopen the resolved ingest.
    assert isinstance(
        _continue_review(
            ingest_service,
            staged.interaction_id,
            confirmer_discord_id=uploader,
        ),
        StagedIngestNotAuthorized,
    )
    assert isinstance(
        _open_fix(
            ingest_service,
            staged.interaction_id,
            confirmer_discord_id=uploader,
        ),
        StagedIngestNotAuthorized,
    )

    identity = PlayerIdentityService(player_repo, pending_repo)
    extraction_after = deserialize_staged_extraction(parent.payload)
    unresolved = [
        *identity.list_unresolved_humans(extraction_after),
    ]
    if not unresolved:
        from scoretopia.domain.actions import UnresolvedPlayerPreview

        unresolved = [
            UnresolvedPlayerPreview(slot_index=0, polytopia_name="Alice"),
        ]
    try:
        identity.begin_identity_check(
            parent_interaction_id=staged.interaction_id,
            uploader_discord_id=uploader,
            extraction=extraction_after,
            unresolved=unresolved,
        )
    except ValueError:
        pass
    else:
        # If begin does not raise, it still must not leave an open identity pending.
        assert pending_repo.list_open_by_kind("confirm_player_link") == [], (
            "identity begin must not open a post-Confirm spelling/link fix"
        )


def test_open_fix_unauthorized_returns_not_authorized(
    ingest_service: IngestService,
    tmp_path: Path,
) -> None:
    extraction = GameBasicsExtraction(
        game_name="Protected",
        players=(GameBasicsPlayer(name="Alice"), GameBasicsPlayer(name="Bob")),
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id="owner-fix",
        extraction=extraction,
        filename="unauth_fix.png",
    )
    result = _open_fix(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id="intruder-fix",
    )
    assert isinstance(result, StagedIngestNotAuthorized)


def test_continue_review_unauthorized_returns_not_authorized(
    ingest_service: IngestService,
    player_repo: PlayerRepo,
    tmp_path: Path,
) -> None:
    extraction = _exact_linked_extraction(
        player_repo,
        uploader_discord_id="owner-continue",
    )
    staged = _stage_game_basics(
        ingest_service,
        tmp_path,
        uploader_discord_id="owner-continue",
        extraction=extraction,
        filename="unauth_continue.png",
    )
    result = _continue_review(
        ingest_service,
        staged.interaction_id,
        confirmer_discord_id="intruder-continue",
    )
    assert isinstance(result, StagedIngestNotAuthorized)
