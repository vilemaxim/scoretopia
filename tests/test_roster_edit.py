"""Tests for full roster shape edit on Fix (Task 039).

Design choices documented for implementers:
- Domain helpers live in ``scoretopia.domain.roster_edit`` (preferred) so
  adapter/views stay thin. Humans-only: Crazy Bot rows are left alone.
- ``add_human_to_staged_roster`` inserts into ``extraction.players`` and
  re-derives / patches ``resolved_roster`` + gating maps so the new slot is
  NEW/fuzzy until Fix-resolved (Continue stays gated).
- ``remove_human_from_staged_roster`` drops a human player_slot and rebuilds
  ``resolved_roster`` / ``fix_resolved_roster_slots`` indexes.
- ``move_human_in_staged_roster`` swaps a human with the adjacent human in
  the requested direction; bot rows stay put relative to humans unless a
  shared helper makes that awkward (document the choice).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from scoretopia.domain.player_resolution import unresolved_fuzzy_new_slot_indexes
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


def _require_roster_edit_api() -> Any:
    try:
        from scoretopia.domain import roster_edit

        return roster_edit
    except ImportError as exc:
        pytest.fail(f"roster_edit module not implemented: {exc}")


def _basics_extraction(*names: str) -> dict[str, object]:
    return {
        "screenshot_type": "game_basics",
        "game_name": "Roster Edit Game",
        "map_size": 12,
        "terrain": "Drylands",
        "game_timer": "Blitz",
        "target_score": 10000,
        "game_type": "Domination",
        "players": [
            {"name": name, "is_you": index == 0} for index, name in enumerate(names)
        ],
    }


def _resolved_slot(
    raw_ocr: str,
    *,
    match_type: str,
    suggested_name: str | None = None,
    confidence: float = 1.0,
) -> dict[str, object]:
    return {
        "raw_ocr": raw_ocr,
        "suggested_name": suggested_name if suggested_name is not None else (
            raw_ocr if match_type == "exact" else None
        ),
        "confidence": confidence,
        "match_type": match_type,
    }


def _parent_with_roster(
    pending_repo: PendingInteractionRepo,
    *,
    uploader: str = "uploader-39",
    player_names: tuple[str, ...] = ("Alice", "Bob"),
    resolved: list[dict[str, object]] | None = None,
    fix_resolved: dict[str, bool] | None = None,
    inbox_path: Path | None = None,
) -> int:
    extraction = _basics_extraction(*player_names)
    if resolved is None:
        resolved = [
            _resolved_slot(name, match_type="exact") for name in player_names
            if not name.lower().startswith("crazy bot")
        ]
    payload: dict[str, object] = {
        "screenshot_type": "game_basics",
        "screenshot_path": str((inbox_path or Path("/tmp")) / "roster_edit.png"),
        "uploader_discord_id": uploader,
        "extraction": extraction,
        "raw_extraction": extraction,
        "resolved_roster": resolved,
        "fix_resolved_roster_slots": {
            str(k): v for k, v in (fix_resolved or {}).items()
        },
        "slot_confirmations": {
            str(i): True for i in range(len(resolved))
        },
    }
    pending = pending_repo.create(
        kind="confirm_extraction",
        discord_user_id=uploader,
        payload=payload,
    )
    return pending.id


def test_add_human_to_staged_roster_increases_length(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    tmp_path: Path,
) -> None:
    module = _require_roster_edit_api()
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice", "Bob"),
        inbox_path=tmp_path,
    )

    new_slot = module.add_human_to_staged_roster(
        pending_repo,
        parent_id,
        name="Carol",
        player_repo=player_repo,
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    players = parent.payload["extraction"]["players"]
    assert isinstance(players, list)
    assert len(players) == 3
    assert players[new_slot]["name"] == "Carol"
    resolved = parent.payload["resolved_roster"]
    assert isinstance(resolved, list)
    assert len(resolved) == 3


def test_added_human_is_unresolved_until_fix_resolved(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    tmp_path: Path,
) -> None:
    module = _require_roster_edit_api()
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice", "Bob"),
        resolved=[
            _resolved_slot("Alice", match_type="exact"),
            _resolved_slot("Bob", match_type="exact"),
        ],
        fix_resolved={0: True, 1: True},
        inbox_path=tmp_path,
    )
    assert unresolved_fuzzy_new_slot_indexes(
        pending_repo.get_by_id(parent_id).payload  # type: ignore[union-attr]
    ) == ()

    module.add_human_to_staged_roster(
        pending_repo,
        parent_id,
        name="BrandNewPlayer",
        player_repo=player_repo,
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    unresolved = unresolved_fuzzy_new_slot_indexes(parent.payload)
    assert unresolved, "new human must gate Continue until Fix-resolved"
    new_entry = parent.payload["resolved_roster"][unresolved[-1]]
    assert new_entry["match_type"] in {"new", "fuzzy"}
    assert new_entry["raw_ocr"] == "BrandNewPlayer"


def test_add_known_name_still_needs_fix_or_is_fuzzy_new_path(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    tmp_path: Path,
) -> None:
    """Added slots enter the normal resolution path (exact may auto-resolve)."""
    module = _require_roster_edit_api()
    player_repo.create(polytopia_name="Carol")
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice",),
        inbox_path=tmp_path,
    )

    module.add_human_to_staged_roster(
        pending_repo,
        parent_id,
        name="Carol",
        player_repo=player_repo,
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    resolved = parent.payload["resolved_roster"]
    assert isinstance(resolved, list)
    assert len(resolved) == 2
    carol = resolved[1]
    assert carol["match_type"] in {"exact", "fuzzy", "new"}
    assert carol["raw_ocr"] == "Carol"
    # Exact may be auto-confirmed; fuzzy/new must appear in unresolved.
    if carol["match_type"] in {"fuzzy", "new"}:
        assert 1 in unresolved_fuzzy_new_slot_indexes(parent.payload)


def test_remove_human_from_staged_roster_decreases_length(
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    module = _require_roster_edit_api()
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice", "JunkName", "Bob"),
        resolved=[
            _resolved_slot("Alice", match_type="exact"),
            _resolved_slot("JunkName", match_type="new", confidence=0.0),
            _resolved_slot("Bob", match_type="exact"),
        ],
        fix_resolved={0: True},
        inbox_path=tmp_path,
    )

    module.remove_human_from_staged_roster(
        pending_repo,
        parent_id,
        player_slot_index=1,
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    players = parent.payload["extraction"]["players"]
    assert [p["name"] for p in players] == ["Alice", "Bob"]
    resolved = parent.payload["resolved_roster"]
    assert [r["raw_ocr"] for r in resolved] == ["Alice", "Bob"]
    assert unresolved_fuzzy_new_slot_indexes(parent.payload) == ()


def test_remove_human_drops_unresolved_slot_indexes(
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    module = _require_roster_edit_api()
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice", "ZedUnknown"),
        resolved=[
            _resolved_slot("Alice", match_type="exact"),
            _resolved_slot("ZedUnknown", match_type="new", confidence=0.0),
        ],
        inbox_path=tmp_path,
    )
    assert unresolved_fuzzy_new_slot_indexes(
        pending_repo.get_by_id(parent_id).payload  # type: ignore[union-attr]
    ) == (1,)

    module.remove_human_from_staged_roster(
        pending_repo,
        parent_id,
        player_slot_index=1,
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    assert unresolved_fuzzy_new_slot_indexes(parent.payload) == ()
    assert len(parent.payload["resolved_roster"]) == 1


def test_move_human_up_reorders_extraction_and_resolved_roster(
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    module = _require_roster_edit_api()
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice", "Bob", "Carol"),
        resolved=[
            _resolved_slot("Alice", match_type="exact"),
            _resolved_slot("Bob", match_type="exact"),
            _resolved_slot("Carol", match_type="exact"),
        ],
        inbox_path=tmp_path,
    )

    module.move_human_in_staged_roster(
        pending_repo,
        parent_id,
        player_slot_index=2,
        direction="up",
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    names = [p["name"] for p in parent.payload["extraction"]["players"]]
    assert names == ["Alice", "Carol", "Bob"]
    resolved_names = [r["raw_ocr"] for r in parent.payload["resolved_roster"]]
    assert resolved_names == ["Alice", "Carol", "Bob"]


def test_move_human_down_reorders_extraction_and_resolved_roster(
    pending_repo: PendingInteractionRepo,
    tmp_path: Path,
) -> None:
    module = _require_roster_edit_api()
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice", "Bob", "Carol"),
        resolved=[
            _resolved_slot("Alice", match_type="exact"),
            _resolved_slot("Bob", match_type="exact"),
            _resolved_slot("Carol", match_type="exact"),
        ],
        inbox_path=tmp_path,
    )

    module.move_human_in_staged_roster(
        pending_repo,
        parent_id,
        player_slot_index=0,
        direction="down",
    )

    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    names = [p["name"] for p in parent.payload["extraction"]["players"]]
    assert names == ["Bob", "Alice", "Carol"]
    resolved_names = [r["raw_ocr"] for r in parent.payload["resolved_roster"]]
    assert resolved_names == ["Bob", "Alice", "Carol"]


def test_roster_shape_edits_leave_bots_in_place(
    pending_repo: PendingInteractionRepo,
    player_repo: PlayerRepo,
    tmp_path: Path,
) -> None:
    """Humans-only edit: Crazy Bot rows stay; humans insert/remove around them."""
    module = _require_roster_edit_api()
    parent_id = _parent_with_roster(
        pending_repo,
        player_names=("Alice", "Crazy Bot", "Bob"),
        resolved=[
            _resolved_slot("Alice", match_type="exact"),
            _resolved_slot("Bob", match_type="exact"),
        ],
        inbox_path=tmp_path,
    )

    module.add_human_to_staged_roster(
        pending_repo,
        parent_id,
        name="Carol",
        player_repo=player_repo,
    )
    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    names_after_add = [p["name"] for p in parent.payload["extraction"]["players"]]
    assert "Crazy Bot" in names_after_add
    assert "Carol" in names_after_add
    assert names_after_add.count("Crazy Bot") == 1

    # Remove Bob (human), bot remains.
    bob_index = names_after_add.index("Bob")
    module.remove_human_from_staged_roster(
        pending_repo,
        parent_id,
        player_slot_index=bob_index,
    )
    parent = pending_repo.get_by_id(parent_id)
    assert parent is not None
    names_after_remove = [p["name"] for p in parent.payload["extraction"]["players"]]
    assert "Crazy Bot" in names_after_remove
    assert "Bob" not in names_after_remove
