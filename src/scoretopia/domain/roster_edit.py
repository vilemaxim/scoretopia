"""Staged roster shape edits during Fix (add / remove / reorder humans).

Humans only: Crazy Bot (and other ``* bot``) rows stay in ``extraction.players``
unless a future task says otherwise. Shape edits re-patch ``resolved_roster``
and remapped ``fix_resolved_roster_slots`` so Continue gating stays correct.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from scoretopia.domain.matching import is_bot_name
from scoretopia.domain.player_resolution import (
    human_roster_index_for_player_slot,
    resolve_roster_slots,
    resolved_roster_as_dicts,
)
from scoretopia.storage.models import PendingInteraction
from scoretopia.storage.repos import PendingInteractionRepo, PlayerRepo

_CONFIRM_EXTRACTION_KIND = "confirm_extraction"
MoveDirection = Literal["up", "down"]


def _require_open_parent(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
) -> tuple[PendingInteraction, dict[str, object], list[dict[str, object]]]:
    parent = pending_repo.get_by_id(parent_id)
    if parent is None or parent.kind != _CONFIRM_EXTRACTION_KIND:
        msg = f"Missing confirm_extraction pending: {parent_id}"
        raise ValueError(msg)
    if parent.status != "open":
        msg = f"confirm_extraction pending is not open: {parent_id}"
        raise ValueError(msg)
    extraction = parent.payload.get("extraction")
    if not isinstance(extraction, dict):
        msg = "Missing extraction payload"
        raise ValueError(msg)
    players = extraction.get("players")
    if not isinstance(players, list):
        msg = "Missing extraction players"
        raise ValueError(msg)
    typed_players = [entry for entry in players if isinstance(entry, dict)]
    if len(typed_players) != len(players):
        msg = "Invalid player entries in extraction"
        raise ValueError(msg)
    return parent, extraction, typed_players


def _human_player_indexes(players: list[dict[str, object]]) -> list[int]:
    return [
        index
        for index, entry in enumerate(players)
        if not is_bot_name(str(entry.get("name", "")))
    ]


def _require_human_slot(
    players: list[dict[str, object]],
    player_slot_index: int,
) -> int:
    if (
        player_slot_index < 0
        or player_slot_index >= len(players)
        or is_bot_name(str(players[player_slot_index].get("name", "")))
    ):
        msg = f"Not a human player slot: {player_slot_index}"
        raise ValueError(msg)
    human_index = human_roster_index_for_player_slot(players, player_slot_index)
    if human_index is None:
        msg = f"Not a human player slot: {player_slot_index}"
        raise ValueError(msg)
    return human_index


def _ensure_resolved_roster(payload: dict[str, object]) -> list[dict[str, object]]:
    resolved = payload.get("resolved_roster")
    if isinstance(resolved, list):
        return [entry for entry in resolved if isinstance(entry, dict)]
    empty: list[dict[str, object]] = []
    payload["resolved_roster"] = empty
    return empty


def _remap_index_map_on_remove(
    index_map: dict[str, object],
    *,
    removed_human_index: int,
) -> dict[str, object]:
    remapped: dict[str, object] = {}
    for key, value in index_map.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if idx == removed_human_index:
            continue
        if idx > removed_human_index:
            remapped[str(idx - 1)] = value
        else:
            remapped[str(idx)] = value
    return remapped


def _swap_index_map_entries(
    index_map: dict[str, object],
    *,
    left: int,
    right: int,
) -> dict[str, object]:
    left_key, right_key = str(left), str(right)
    remapped = dict(index_map)
    left_val = remapped.pop(left_key, None)
    right_val = remapped.pop(right_key, None)
    if right_val is not None:
        remapped[left_key] = right_val
    if left_val is not None:
        remapped[right_key] = left_val
    return remapped


def _rewrite_human_index_maps(
    payload: dict[str, object],
    *,
    transform: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    for key in ("fix_resolved_roster_slots", "slot_confirmations"):
        current = payload.get(key)
        if isinstance(current, dict):
            payload[key] = transform(current)


def _persist_players(
    pending_repo: PendingInteractionRepo,
    parent: PendingInteraction,
    extraction: dict[str, object],
    players: list[dict[str, object]],
) -> None:
    extraction["players"] = players
    parent.payload["extraction"] = extraction
    pending_repo.update_payload(parent.id, parent.payload)


def add_human_to_staged_roster(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
    *,
    name: str,
    player_repo: PlayerRepo,
) -> int:
    """Append a human to the staged roster; returns new ``players`` index."""
    cleaned = name.strip()
    if not cleaned or is_bot_name(cleaned):
        msg = f"Invalid human player name: {name!r}"
        raise ValueError(msg)

    parent, extraction, players = _require_open_parent(pending_repo, parent_id)
    screenshot_type = str(
        parent.payload.get("screenshot_type")
        or extraction.get("screenshot_type")
        or "game_basics"
    )
    new_entry: dict[str, object] = {"name": cleaned, "is_you": False}
    if extraction.get("screenshot_type") == "game_end" or any(
        "score" in entry for entry in players
    ):
        new_entry["score"] = None
        new_entry["is_winner"] = False

    players.append(new_entry)
    new_slot = len(players) - 1

    resolved_slots = resolve_roster_slots(
        [cleaned],
        player_repo,
        screenshot_type=screenshot_type,
    )
    resolved = _ensure_resolved_roster(parent.payload)
    resolved.extend(resolved_roster_as_dicts(resolved_slots))
    parent.payload["resolved_roster"] = resolved

    confirmations = parent.payload.get("slot_confirmations")
    if not isinstance(confirmations, dict):
        confirmations = {}
    new_human_index = len(resolved) - 1
    # Exact matches do not need Fix; fuzzy/new stay unconfirmed.
    confirmations[str(new_human_index)] = bool(
        resolved_slots and resolved_slots[0].match_type == "exact"
    )
    parent.payload["slot_confirmations"] = confirmations

    _persist_players(pending_repo, parent, extraction, players)
    return new_slot


def remove_human_from_staged_roster(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
    *,
    player_slot_index: int,
) -> None:
    parent, extraction, players = _require_open_parent(pending_repo, parent_id)
    human_index = _require_human_slot(players, player_slot_index)

    del players[player_slot_index]

    resolved = _ensure_resolved_roster(parent.payload)
    if human_index < len(resolved):
        del resolved[human_index]
    parent.payload["resolved_roster"] = resolved

    _rewrite_human_index_maps(
        parent.payload,
        transform=lambda mapping: _remap_index_map_on_remove(
            mapping,
            removed_human_index=human_index,
        ),
    )
    _persist_players(pending_repo, parent, extraction, players)


def move_human_in_staged_roster(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
    *,
    player_slot_index: int,
    direction: MoveDirection,
) -> None:
    parent, extraction, players = _require_open_parent(pending_repo, parent_id)
    human_index = _require_human_slot(players, player_slot_index)
    human_slots = _human_player_indexes(players)

    if direction == "up":
        if human_index == 0:
            return
        other_human_index = human_index - 1
    elif direction == "down":
        if human_index >= len(human_slots) - 1:
            return
        other_human_index = human_index + 1
    else:
        msg = f"Unknown move direction: {direction!r}"
        raise ValueError(msg)

    other_slot = human_slots[other_human_index]
    players[player_slot_index], players[other_slot] = (
        players[other_slot],
        players[player_slot_index],
    )

    resolved = _ensure_resolved_roster(parent.payload)
    if human_index < len(resolved) and other_human_index < len(resolved):
        resolved[human_index], resolved[other_human_index] = (
            resolved[other_human_index],
            resolved[human_index],
        )
    parent.payload["resolved_roster"] = resolved

    _rewrite_human_index_maps(
        parent.payload,
        transform=lambda mapping: _swap_index_map_entries(
            mapping,
            left=human_index,
            right=other_human_index,
        ),
    )
    _persist_players(pending_repo, parent, extraction, players)
