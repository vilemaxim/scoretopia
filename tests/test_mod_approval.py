"""Tests for bot-mod authorization and correction-session batching (Task 029)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from scoretopia.screenshot.models import GameBasicsExtraction, GameBasicsPlayer
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import PendingInteractionRepo, PlayerRepo


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
def inbox_path(tmp_path: Path) -> Path:
    path = tmp_path / "inbox"
    path.mkdir()
    return path


def _require_mod_approval_api() -> Any:
    try:
        from scoretopia.domain import mod_approval

        return mod_approval
    except ImportError as exc:
        pytest.fail(f"mod_approval module not implemented: {exc}")


def _bot_mods_config(*discord_user_ids: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        bot_mods=SimpleNamespace(discord_user_ids=tuple(discord_user_ids)),
        training=SimpleNamespace(path=Path("data/training")),
    )


def _game_basics(*names: str, is_you_index: int = 0) -> GameBasicsExtraction:
    return GameBasicsExtraction(
        game_name="Mod Approval Game",
        players=tuple(
            GameBasicsPlayer(name=name, is_you=(index == is_you_index))
            for index, name in enumerate(names)
        ),
    )


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
            "screenshot_path": str(inbox_path / "mod_approval.png"),
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


def _service(
    pending_repo: PendingInteractionRepo,
    *,
    config: Any,
    player_repo: PlayerRepo | None = None,
) -> Any:
    module = _require_mod_approval_api()
    return module.ModApprovalService(
        pending_repo,
        config=config,
        player_repo=player_repo,
    )


def test_mod_name_correction_applies_immediately_without_mod_approval_pending(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    config = _bot_mods_config(mod_id)
    service = _service(pending_repo, config=config, player_repo=player_repo)
    player_repo.create(polytopia_name="Uploader", discord_user_id=mod_id)
    player_repo.create(polytopia_name="RealBob")
    extraction = _game_basics("Uploader", "WrngBob", is_you_index=0)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=mod_id,
        extraction=extraction,
        inbox_path=inbox_path,
    )

    result = service.queue_name_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=mod_id,
        slot_index=1,
        old_name="WrngBob",
        new_name="RealBob",
    )

    assert result is None or getattr(result, "action", None) != (
        "mod_approval_needs_confirmation"
    )
    assert pending_repo.list_open_by_kind("mod_approval") == []

    from scoretopia.domain.ingest import deserialize_staged_extraction

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    staged = deserialize_staged_extraction(parent.payload)
    assert staged.players[1].name == "RealBob"


def test_non_mod_name_correction_creates_mod_approval_pending_with_batch_payload(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    uploader_id = "999999999999999999"
    config = _bot_mods_config(mod_id)
    service = _service(pending_repo, config=config, player_repo=player_repo)
    player_repo.create(polytopia_name="Uploader", discord_user_id=uploader_id)
    extraction = _game_basics("Uploader", "WrngBob", is_you_index=0)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=uploader_id,
        extraction=extraction,
        inbox_path=inbox_path,
    )

    service.queue_name_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=uploader_id,
        slot_index=1,
        old_name="WrngBob",
        new_name="RealBob",
    )
    result = service.submit_for_approval(
        parent_interaction_id=parent_id,
        uploader_discord_id=uploader_id,
    )

    assert result.action == "mod_approval_needs_confirmation"
    open_approvals = pending_repo.list_open_by_kind("mod_approval")
    assert len(open_approvals) == 1
    pending = open_approvals[0]
    assert pending.id == result.interaction_id
    assert pending.payload.get("parent_extraction_interaction_id") == parent_id
    session = pending.payload.get("correction_session") or pending.payload.get(
        "corrections"
    )
    assert session is not None
    serialized = str(session)
    assert "WrngBob" in serialized
    assert "RealBob" in serialized

    from scoretopia.domain.ingest import deserialize_staged_extraction

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    # Parent extraction stays unchanged until mod approves.
    staged = deserialize_staged_extraction(parent.payload)
    assert staged.players[1].name == "WrngBob"


def test_mod_approve_applies_all_batched_corrections_to_parent_extraction(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    uploader_id = "999999999999999999"
    config = _bot_mods_config(mod_id)
    service = _service(pending_repo, config=config, player_repo=player_repo)
    player_repo.create(polytopia_name="Uploader", discord_user_id=uploader_id)
    extraction = _game_basics("Uploader", "WrngBob", "TypoAlice", is_you_index=0)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=uploader_id,
        extraction=extraction,
        inbox_path=inbox_path,
    )

    service.queue_name_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=uploader_id,
        slot_index=1,
        old_name="WrngBob",
        new_name="RealBob",
    )
    service.queue_name_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=uploader_id,
        slot_index=2,
        old_name="TypoAlice",
        new_name="Alice",
    )
    submitted = service.submit_for_approval(
        parent_interaction_id=parent_id,
        uploader_discord_id=uploader_id,
    )

    service.approve(
        submitted.interaction_id,
        approver_discord_id=mod_id,
    )

    assert pending_repo.list_open_by_kind("mod_approval") == []
    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    assert parent.status == "open"

    from scoretopia.domain.ingest import deserialize_staged_extraction

    staged = deserialize_staged_extraction(parent.payload)
    assert staged.players[1].name == "RealBob"
    assert staged.players[2].name == "Alice"


def test_mod_reject_does_not_mutate_domain_state(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    inbox_path: Path,
) -> None:
    mod_id = "111111111111111111"
    uploader_id = "999999999999999999"
    config = _bot_mods_config(mod_id)
    service = _service(pending_repo, config=config, player_repo=player_repo)
    player_repo.create(polytopia_name="Uploader", discord_user_id=uploader_id)
    extraction = _game_basics("Uploader", "WrngBob", is_you_index=0)
    parent_id = _parent_with_staged_extraction(
        pending_repo,
        uploader_discord_id=uploader_id,
        extraction=extraction,
        inbox_path=inbox_path,
    )

    service.queue_name_correction(
        parent_interaction_id=parent_id,
        actor_discord_id=uploader_id,
        slot_index=1,
        old_name="WrngBob",
        new_name="RealBob",
    )
    submitted = service.submit_for_approval(
        parent_interaction_id=parent_id,
        uploader_discord_id=uploader_id,
    )

    service.reject(
        submitted.interaction_id,
        rejector_discord_id=mod_id,
    )

    assert pending_repo.list_open_by_kind("mod_approval") == []
    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    assert parent.status == "open"

    from scoretopia.domain.ingest import deserialize_staged_extraction

    staged = deserialize_staged_extraction(parent.payload)
    assert staged.players[1].name == "WrngBob"
    assert player_repo.get_by_polytopia_name("RealBob") is None
