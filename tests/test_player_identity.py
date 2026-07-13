"""Tests for unknown-player identity verification (Task 018)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService
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


def _require_player_identity_module():
    try:
        from scoretopia.domain import player_identity

        return player_identity
    except ImportError as exc:
        pytest.fail(f"player_identity module not implemented: {exc}")


def _player_identity_service(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
):
    module = _require_player_identity_module()
    return module.PlayerIdentityService(player_repo, pending_repo)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def player_repo(conn: sqlite3.Connection) -> PlayerRepo:
    return PlayerRepo(conn)


@pytest.fixture
def pending_repo(conn: sqlite3.Connection) -> PendingInteractionRepo:
    return PendingInteractionRepo(conn)


@pytest.fixture
def player_service(player_repo: PlayerRepo) -> PlayerService:
    return PlayerService(player_repo)


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


def _game_basics(*names: str, is_you_index: int | None = None) -> GameBasicsExtraction:
    players = []
    for index, name in enumerate(names):
        players.append(
            GameBasicsPlayer(
                name=name,
                is_you=(index == is_you_index),
            )
        )
    return GameBasicsExtraction(
        game_name="Identity Test Game",
        players=tuple(players),
    )


def test_list_unresolved_humans_detects_new_name(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    extraction = _game_basics("Alice", "BrandNewBob")

    unresolved = player_identity_service.list_unresolved_humans(extraction)

    assert len(unresolved) == 2
    names = {entry.polytopia_name for entry in unresolved}
    assert names == {"Alice", "BrandNewBob"}
    by_name = {entry.polytopia_name: entry for entry in unresolved}
    assert by_name["BrandNewBob"].player_id is None
    assert by_name["BrandNewBob"].slot_index == 1


def test_list_unresolved_humans_detects_unlinked_existing_row(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    existing = player_repo.create(polytopia_name="UnlinkedCarol")
    extraction = _game_basics("UnlinkedCarol", "Dave")

    unresolved = player_identity_service.list_unresolved_humans(extraction)

    assert len(unresolved) == 2
    carol = next(u for u in unresolved if u.polytopia_name == "UnlinkedCarol")
    assert carol.player_id == existing.id
    assert carol.slot_index == 0


def test_list_unresolved_humans_skips_linked_humans_and_bots(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    player_repo.create(
        polytopia_name="LinkedAlice",
        discord_user_id="discord-alice",
    )
    player_repo.create(polytopia_name="LinkedBob", discord_user_id="discord-bob")

    extraction = _game_basics("LinkedAlice", "LinkedBob", "Crazy Bot", "Hard Bot")

    unresolved = player_identity_service.list_unresolved_humans(extraction)

    assert unresolved == []


def test_begin_identity_check_creates_confirm_player_link_pending(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    extraction = _game_basics("NewPlayer")
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id="uploader-1",
        payload={"screenshot_type": "game_basics"},
    )

    result = player_identity_service.begin_identity_check(
        parent_interaction_id=parent.id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )

    assert isinstance(result, module.PlayerLinkNeedsConfirmation)
    assert result.parent_extraction_interaction_id == parent.id
    assert result.interaction_id > 0
    assert len(result.unresolved) == 1
    assert result.unresolved[0].polytopia_name == "NewPlayer"

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.kind == "confirm_player_link"
    assert pending.discord_user_id == "uploader-1"
    assert pending.status == "open"
    assert pending.payload["parent_extraction_interaction_id"] == parent.id


def test_remote_confirm_creates_discord_link_and_completes_ingest_chain(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    player_service: PlayerService,
    game_service: GameService,
    win_ratio_service: WinRatioService,
    inbox_path: Path,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    player_repo.create(
        polytopia_name="Uploader",
        discord_user_id="uploader-1",
    )
    extraction = _game_basics("Uploader", "NewBob", is_you_index=0)
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id="uploader-1",
        payload={
            "screenshot_type": "game_basics",
            "screenshot_path": str(inbox_path / "remote_confirm.png"),
            "uploader_discord_id": "uploader-1",
            "extraction": {
                "screenshot_type": "game_basics",
                "game_name": extraction.game_name,
                "players": [
                    {
                        "name": p.name,
                        "is_you": p.is_you,
                        "is_eliminated": p.is_eliminated,
                    }
                    for p in extraction.players
                ],
            },
        },
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent.id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )
    player_identity_service.confirm_spelling(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="uploader-1",
    )
    player_identity_service.select_discord_user(
        identity.interaction_id,
        slot_index=1,
        selected_discord_user_id="bob-discord",
        confirmer_discord_id="uploader-1",
    )

    result = player_identity_service.confirm_remote_link(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="bob-discord",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    linked = player_repo.get_by_polytopia_name("NewBob")
    assert linked is not None
    assert linked.discord_user_id == "bob-discord"

    ingest = IngestService(
        player_service=player_service,
        game_service=game_service,
        win_ratio_service=win_ratio_service,
        pending_repo=pending_repo,
        inbox_path=inbox_path,
    )
    committed = ingest.continue_review(
        parent.id,
        confirmer_discord_id="uploader-1",
    )
    from scoretopia.domain.actions import FinalSummaryNeedsConfirmation, GameStarted

    if isinstance(committed, FinalSummaryNeedsConfirmation):
        committed = ingest.confirm_final_summary(
            committed.interaction_id,
            confirmer_discord_id="uploader-1",
        )

    assert isinstance(committed, GameStarted)
    assert committed.game.name == "Identity Test Game"


def test_remote_confirm_blocked_when_name_owned_by_another_discord_user(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    existing = player_repo.create(polytopia_name="TakenName")
    extraction = _game_basics("TakenName")
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id="uploader-1",
        payload={"screenshot_type": "game_basics"},
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent.id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )
    player_identity_service.confirm_spelling(
        identity.interaction_id,
        slot_index=0,
        confirmer_discord_id="uploader-1",
    )
    player_identity_service.select_discord_user(
        identity.interaction_id,
        slot_index=0,
        selected_discord_user_id="impostor-discord",
        confirmer_discord_id="uploader-1",
    )
    player_repo.update_discord_link(
        existing.id,
        discord_user_id="owner-discord",
        discord_display_name="owner",
    )

    result = player_identity_service.confirm_remote_link(
        identity.interaction_id,
        slot_index=0,
        confirmer_discord_id="impostor-discord",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.BLOCKED
    assert result.blocked_owner_discord_id == "owner-discord"
    owner = player_repo.get_by_polytopia_name("TakenName")
    assert owner is not None
    assert owner.discord_user_id == "owner-discord"


# --- Wrong OCR spelling — pick known player (Task 019) ---


def _parent_with_staged_extraction(
    pending_repo: PendingInteractionRepo,
    *,
    uploader_discord_id: str,
    extraction: GameBasicsExtraction,
    inbox_path: Path,
) -> int:
    pending = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id=uploader_discord_id,
        payload={
            "screenshot_type": "game_basics",
            "screenshot_path": str(inbox_path / "wrong_ocr.png"),
            "uploader_discord_id": uploader_discord_id,
            "extraction": {
                "screenshot_type": "game_basics",
                "game_name": extraction.game_name,
                "players": [
                    {
                        "name": player.name,
                        "is_you": player.is_you,
                        "is_eliminated": player.is_eliminated,
                    }
                    for player in extraction.players
                ],
            },
        },
    )
    return pending.id


def _slot_for_index(
    pending_repo: PendingInteractionRepo,
    interaction_id: int,
    slot_index: int,
) -> dict[str, object]:
    pending = pending_repo.get_by_id(interaction_id)
    assert pending is not None
    slots = pending.payload.get("slots")
    assert isinstance(slots, list)
    for slot in slots:
        if isinstance(slot, dict) and slot.get("slot_index") == slot_index:
            return slot
    msg = f"Missing slot payload for index {slot_index}"
    raise AssertionError(msg)


def test_reject_spelling_pick_canonical_updates_staged_payload_before_commit(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    player_repo.create(polytopia_name="Uploader", discord_user_id="uploader-1")
    canonical = player_repo.create(polytopia_name="RealBob")
    extraction = _game_basics("Uploader", "WrngBob", is_you_index=0)
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        inbox_path=inbox_path,
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent_id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )

    player_identity_service.reject_spelling(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="uploader-1",
    )
    player_identity_service.pick_canonical_player(
        identity.interaction_id,
        slot_index=1,
        player_id=canonical.id,
        picker_discord_id="uploader-1",
    )

    slot = _slot_for_index(pending_repo, identity.interaction_id, slot_index=1)
    assert slot["polytopia_name"] == "RealBob"
    assert slot["player_id"] == canonical.id

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    from scoretopia.domain.ingest import deserialize_staged_extraction

    staged = deserialize_staged_extraction(parent.payload)
    assert staged.players[1].name == "RealBob"


def test_pick_linked_player_requires_remote_confirm(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    player_repo.create(polytopia_name="Uploader", discord_user_id="uploader-1")
    linked = player_repo.create(
        polytopia_name="Alice",
        discord_user_id="alice-discord",
    )
    extraction = _game_basics("Uploader", "Alce", is_you_index=0)
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        inbox_path=inbox_path,
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent_id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )

    player_identity_service.reject_spelling(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="uploader-1",
    )
    player_identity_service.pick_canonical_player(
        identity.interaction_id,
        slot_index=1,
        player_id=linked.id,
        picker_discord_id="uploader-1",
    )

    slot = _slot_for_index(pending_repo, identity.interaction_id, slot_index=1)
    assert slot["selected_discord_user_id"] == "alice-discord"
    assert not slot["resolved"]

    result = player_identity_service.confirm_remote_link(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="alice-discord",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    slot_after = _slot_for_index(
        pending_repo,
        identity.interaction_id,
        slot_index=1,
    )
    assert slot_after["resolved"] is True


def test_pick_unlinked_player_requires_uploader_discord_then_remote_confirm(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    inbox_path: Path,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    player_repo.create(polytopia_name="Uploader", discord_user_id="uploader-1")
    unlinked = player_repo.create(polytopia_name="Carol")
    extraction = _game_basics("Uploader", "Crol", is_you_index=0)
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        inbox_path=inbox_path,
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent_id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )

    player_identity_service.reject_spelling(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="uploader-1",
    )
    player_identity_service.pick_canonical_player(
        identity.interaction_id,
        slot_index=1,
        player_id=unlinked.id,
        picker_discord_id="uploader-1",
    )

    slot = _slot_for_index(pending_repo, identity.interaction_id, slot_index=1)
    assert slot["selected_discord_user_id"] is None
    assert not slot["resolved"]

    player_identity_service.select_discord_user(
        identity.interaction_id,
        slot_index=1,
        selected_discord_user_id="carol-discord",
        confirmer_discord_id="uploader-1",
    )
    result = player_identity_service.confirm_remote_link(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="carol-discord",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    linked = player_repo.get_by_polytopia_name("Carol")
    assert linked is not None
    assert linked.discord_user_id == "carol-discord"


def _begin_slot_with_selected_discord(
    player_identity_service,
    pending_repo: PendingInteractionRepo,
    *,
    polytopia_name: str,
    selected_discord_user_id: str,
    uploader_discord_id: str = "uploader-1",
) -> tuple[int, int]:
    extraction = _game_basics(polytopia_name)
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id=uploader_discord_id,
        payload={"screenshot_type": "game_basics"},
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent.id,
        uploader_discord_id=uploader_discord_id,
        extraction=extraction,
        unresolved=unresolved,
    )
    slot_index = unresolved[0].slot_index
    player_identity_service.confirm_spelling(
        identity.interaction_id,
        slot_index=slot_index,
        confirmer_discord_id=uploader_discord_id,
    )
    player_identity_service.select_discord_user(
        identity.interaction_id,
        slot_index=slot_index,
        selected_discord_user_id=selected_discord_user_id,
        confirmer_discord_id=uploader_discord_id,
    )
    return identity.interaction_id, slot_index


def test_link_discord_already_on_other_player_returns_needs_override(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    owner = player_repo.create(
        polytopia_name="RegisteredName",
        discord_user_id="shared-discord",
    )
    interaction_id, slot_index = _begin_slot_with_selected_discord(
        player_identity_service,
        pending_repo,
        polytopia_name="vilemaxim1",
        selected_discord_user_id="shared-discord",
    )

    result = player_identity_service.link_selected_discord_user(
        interaction_id,
        slot_index=slot_index,
        override=False,
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.NEEDS_OVERRIDE
    assert result.current_owner_polytopia_name == "RegisteredName"
    assert result.current_owner_player_id == owner.id
    assert player_repo.get_by_discord_id("shared-discord") is not None
    assert player_repo.get_by_discord_id("shared-discord").id == owner.id
    slot = _slot_for_index(pending_repo, interaction_id, slot_index)
    assert slot["resolved"] is False


def test_override_clears_old_discord_link_and_attaches_to_target(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    owner = player_repo.create(
        polytopia_name="RegisteredName",
        discord_user_id="shared-discord",
    )
    interaction_id, slot_index = _begin_slot_with_selected_discord(
        player_identity_service,
        pending_repo,
        polytopia_name="vilemaxim1",
        selected_discord_user_id="shared-discord",
    )

    result = player_identity_service.link_selected_discord_user(
        interaction_id,
        slot_index=slot_index,
        override=True,
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    cleared = player_repo.get_by_id(owner.id)
    assert cleared is not None
    assert cleared.discord_user_id is None
    linked = player_repo.get_by_polytopia_name("vilemaxim1")
    assert linked is not None
    assert linked.discord_user_id == "shared-discord"
    assert linked.id != owner.id


def test_confirm_remote_link_discord_conflict_needs_override_not_integrity_error(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    player_repo.create(
        polytopia_name="OtherRow",
        discord_user_id="bob-discord",
    )
    interaction_id, slot_index = _begin_slot_with_selected_discord(
        player_identity_service,
        pending_repo,
        polytopia_name="NewBob",
        selected_discord_user_id="bob-discord",
    )

    result = player_identity_service.confirm_remote_link(
        interaction_id,
        slot_index=slot_index,
        confirmer_discord_id="bob-discord",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.NEEDS_OVERRIDE
    assert result.current_owner_polytopia_name == "OtherRow"


def test_link_idempotent_when_discord_already_on_same_player(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    existing = player_repo.create(polytopia_name="SamePlayer")
    interaction_id, slot_index = _begin_slot_with_selected_discord(
        player_identity_service,
        pending_repo,
        polytopia_name="SamePlayer",
        selected_discord_user_id="same-discord",
    )
    player_repo.update_discord_link(
        existing.id,
        discord_user_id="same-discord",
        discord_display_name=None,
    )

    result = player_identity_service.link_selected_discord_user(
        interaction_id,
        slot_index=slot_index,
        override=False,
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    slot = _slot_for_index(pending_repo, interaction_id, slot_index)
    assert slot["resolved"] is True


# --- Skip Discord link during identity check (Task 001) ---


def _begin_identity_after_spelling(
    player_identity_service,
    pending_repo: PendingInteractionRepo,
    *,
    names: tuple[str, ...],
    uploader_discord_id: str = "uploader-1",
    confirm_slot_indexes: tuple[int, ...] | None = None,
) -> tuple[int, int]:
    """Begin identity check and optionally confirm spelling on slots.

    Returns ``(identity_interaction_id, parent_interaction_id)``.
    """
    extraction = _game_basics(*names)
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id=uploader_discord_id,
        payload={"screenshot_type": "game_basics"},
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent.id,
        uploader_discord_id=uploader_discord_id,
        extraction=extraction,
        unresolved=unresolved,
    )
    slots_to_confirm = (
        confirm_slot_indexes
        if confirm_slot_indexes is not None
        else tuple(entry.slot_index for entry in unresolved)
    )
    for slot_index in slots_to_confirm:
        player_identity_service.confirm_spelling(
            identity.interaction_id,
            slot_index=slot_index,
            confirmer_discord_id=uploader_discord_id,
        )
    return identity.interaction_id, parent.id


def test_skip_discord_resolves_last_slot_creates_unlinked_player(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    interaction_id, _parent_id = _begin_identity_after_spelling(
        player_identity_service,
        pending_repo,
        names=("SkipOnlyBob",),
    )

    result = player_identity_service.skip_discord_link(
        interaction_id,
        slot_index=0,
        confirmer_discord_id="uploader-1",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    slot = _slot_for_index(pending_repo, interaction_id, slot_index=0)
    assert slot["resolved"] is True
    assert slot.get("selected_discord_user_id") in (None, "")
    player = player_repo.get_by_polytopia_name("SkipOnlyBob")
    assert player is not None
    assert player.discord_user_id is None
    pending = pending_repo.get_by_id(interaction_id)
    assert pending is not None
    assert pending.status == "resolved"


def test_skip_discord_keeps_existing_unlinked_player_row(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    existing = player_repo.create(polytopia_name="AlreadyThere")
    interaction_id, _parent_id = _begin_identity_after_spelling(
        player_identity_service,
        pending_repo,
        names=("AlreadyThere",),
    )

    result = player_identity_service.skip_discord_link(
        interaction_id,
        slot_index=0,
        confirmer_discord_id="uploader-1",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    player = player_repo.get_by_id(existing.id)
    assert player is not None
    assert player.discord_user_id is None
    assert player.polytopia_name == "AlreadyThere"
    slot = _slot_for_index(pending_repo, interaction_id, slot_index=0)
    assert slot["resolved"] is True
    assert slot["player_id"] == existing.id


def test_skip_discord_does_not_clear_existing_discord_link(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    # Slot starts unresolved; Discord is attached after identity begins so skip
    # must not wipe an existing link if one is somehow already set.
    extraction = _game_basics("LinkedLater")
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id="uploader-1",
        payload={"screenshot_type": "game_basics"},
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent.id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )
    player_identity_service.confirm_spelling(
        identity.interaction_id,
        slot_index=0,
        confirmer_discord_id="uploader-1",
    )
    linked = player_repo.create(
        polytopia_name="LinkedLater",
        discord_user_id="keep-this-discord",
    )

    result = player_identity_service.skip_discord_link(
        identity.interaction_id,
        slot_index=0,
        confirmer_discord_id="uploader-1",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    player = player_repo.get_by_id(linked.id)
    assert player is not None
    assert player.discord_user_id == "keep-this-discord"
    slot = _slot_for_index(pending_repo, identity.interaction_id, slot_index=0)
    assert slot["resolved"] is True


def test_skip_discord_with_remaining_slots_leaves_pending_open(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    interaction_id, _parent_id = _begin_identity_after_spelling(
        player_identity_service,
        pending_repo,
        names=("SkipAlice", "LaterBob"),
        confirm_slot_indexes=(0,),
    )

    result = player_identity_service.skip_discord_link(
        interaction_id,
        slot_index=0,
        confirmer_discord_id="uploader-1",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS
    pending = pending_repo.get_by_id(interaction_id)
    assert pending is not None
    assert pending.status == "open"
    skipped = _slot_for_index(pending_repo, interaction_id, slot_index=0)
    remaining = _slot_for_index(pending_repo, interaction_id, slot_index=1)
    assert skipped["resolved"] is True
    assert remaining["resolved"] is False
    still_open = player_identity_service.find_pending_for_parent(_parent_id)
    assert still_open is not None
    assert len(still_open.unresolved) == 1
    assert still_open.unresolved[0].polytopia_name == "LaterBob"


def test_skip_discord_unauthorized_confirmer_does_not_resolve(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    interaction_id, _parent_id = _begin_identity_after_spelling(
        player_identity_service,
        pending_repo,
        names=("GuardedName",),
    )

    result = player_identity_service.skip_discord_link(
        interaction_id,
        slot_index=0,
        confirmer_discord_id="intruder-99",
    )

    assert result.outcome == module.ConfirmPlayerLinkOutcome.NOT_AUTHORIZED
    slot = _slot_for_index(pending_repo, interaction_id, slot_index=0)
    assert slot["resolved"] is False
    assert player_repo.get_by_polytopia_name("GuardedName") is None
    pending = pending_repo.get_by_id(interaction_id)
    assert pending is not None
    assert pending.status == "open"


def test_skip_discord_then_continue_review_completes_with_unlinked_player(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    player_service: PlayerService,
    game_service: GameService,
    win_ratio_service: WinRatioService,
    inbox_path: Path,
) -> None:
    player_identity_service = _player_identity_service(player_repo, pending_repo)
    module = _require_player_identity_module()
    player_repo.create(
        polytopia_name="Uploader",
        discord_user_id="uploader-1",
    )
    extraction = _game_basics("Uploader", "NoDiscordCarol", is_you_index=0)
    unresolved = player_identity_service.list_unresolved_humans(extraction)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        inbox_path=inbox_path,
    )
    identity = player_identity_service.begin_identity_check(
        parent_interaction_id=parent_id,
        uploader_discord_id="uploader-1",
        extraction=extraction,
        unresolved=unresolved,
    )
    player_identity_service.confirm_spelling(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="uploader-1",
    )

    result = player_identity_service.skip_discord_link(
        identity.interaction_id,
        slot_index=1,
        confirmer_discord_id="uploader-1",
    )
    assert result.outcome == module.ConfirmPlayerLinkOutcome.SUCCESS

    ingest = IngestService(
        player_service=player_service,
        game_service=game_service,
        win_ratio_service=win_ratio_service,
        pending_repo=pending_repo,
        inbox_path=inbox_path,
        player_identity_service=player_identity_service,
    )
    continued = ingest.continue_review(
        parent_id,
        confirmer_discord_id="uploader-1",
    )
    from scoretopia.domain.actions import FinalSummaryNeedsConfirmation, GameStarted

    if isinstance(continued, FinalSummaryNeedsConfirmation):
        continued = ingest.confirm_final_summary(
            continued.interaction_id,
            confirmer_discord_id="uploader-1",
        )

    assert isinstance(continued, GameStarted)
    carol = player_repo.get_by_polytopia_name("NoDiscordCarol")
    assert carol is not None
    assert carol.discord_user_id is None
