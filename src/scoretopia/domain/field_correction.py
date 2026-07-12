"""Field-by-field correction of staged screenshot extractions."""

from __future__ import annotations

import logging
from typing import Protocol

from scoretopia.config import is_bot_mod
from scoretopia.domain.actions import ModApprovalNeedsConfirmation
from scoretopia.domain.mod_approval import ModApprovalService
from scoretopia.domain.player_resolution import mark_roster_slot_fix_resolved
from scoretopia.storage.models import PendingInteraction
from scoretopia.storage.repos import PendingInteractionRepo

logger = logging.getLogger(__name__)

_CONFIRM_EXTRACTION_KIND = "confirm_extraction"


class _HasBotMods(Protocol):
    @property
    def bot_mods(self) -> object: ...


def _correction_entry(
    *,
    field: str,
    old: object,
    new: object,
    slot_index: int | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "kind": "field_correction",
        "field": field,
        "old": old,
        "new": new,
    }
    if slot_index is not None:
        entry["slot_index"] = slot_index
    return entry


def _parent_corrections(payload: dict[str, object]) -> list[dict[str, object]]:
    corrections = payload.get("corrections")
    if isinstance(corrections, list):
        return [entry for entry in corrections if isinstance(entry, dict)]
    return []


def _require_player_slot(
    extraction: dict[str, object],
    slot_index: int,
) -> dict[str, object]:
    players = extraction.get("players")
    if (
        not isinstance(players, list)
        or slot_index < 0
        or slot_index >= len(players)
    ):
        msg = f"Unknown player slot: {slot_index}"
        raise ValueError(msg)
    entry = players[slot_index]
    if not isinstance(entry, dict):
        msg = f"Invalid player entry at slot {slot_index}"
        raise ValueError(msg)
    return entry


def apply_field_correction_to_extraction(
    extraction: dict[str, object],
    *,
    field: str,
    new: object,
    slot_index: int | None = None,
) -> None:
    """Mutate a serialized extraction dict in place for one field correction."""
    if field == "score":
        if slot_index is None:
            msg = "score correction requires slot_index"
            raise ValueError(msg)
        _require_player_slot(extraction, slot_index)["score"] = new
        return

    if field == "winner":
        extraction["winner"] = new
        players = extraction.get("players")
        if isinstance(players, list) and isinstance(new, str):
            for entry in players:
                if isinstance(entry, dict):
                    entry["is_winner"] = entry.get("name") == new
        return

    if field == "players" and slot_index is not None:
        _require_player_slot(extraction, slot_index)["name"] = new
        return

    extraction[field] = new


def apply_field_correction_to_parent(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
    *,
    field: str,
    new: object,
    slot_index: int | None = None,
) -> None:
    parent = pending_repo.get_by_id(parent_id)
    if parent is None or parent.kind != _CONFIRM_EXTRACTION_KIND:
        msg = f"Missing confirm_extraction pending: {parent_id}"
        raise ValueError(msg)
    extraction = parent.payload.get("extraction")
    if not isinstance(extraction, dict):
        msg = "Missing extraction payload"
        raise ValueError(msg)
    apply_field_correction_to_extraction(
        extraction,
        field=field,
        new=new,
        slot_index=slot_index,
    )
    if field == "players" and slot_index is not None:
        mark_roster_slot_fix_resolved(
            parent.payload,
            player_slot_index=slot_index,
        )
    pending_repo.update_payload(parent_id, parent.payload)


def _append_parent_correction(
    pending_repo: PendingInteractionRepo,
    parent: PendingInteraction,
    entry: dict[str, object],
) -> None:
    corrections = _parent_corrections(parent.payload)
    corrections.append(entry)
    parent.payload["corrections"] = corrections
    pending_repo.update_payload(parent.id, parent.payload)


def _queue_in_correction_session(
    pending_repo: PendingInteractionRepo,
    parent: PendingInteraction,
    entry: dict[str, object],
) -> None:
    session = parent.payload.get("correction_session")
    if not isinstance(session, dict):
        session = {"corrections": []}
    corrections = session.get("corrections")
    if not isinstance(corrections, list):
        corrections = []
    corrections.append(entry)
    session["corrections"] = corrections
    parent.payload["correction_session"] = session
    # Also mirror on top-level corrections for review UI.
    top = _parent_corrections(parent.payload)
    top.append(entry)
    parent.payload["corrections"] = top
    pending_repo.update_payload(parent.id, parent.payload)


class FieldCorrectionService:
    def __init__(
        self,
        pending_repo: PendingInteractionRepo,
        *,
        config: _HasBotMods,
    ) -> None:
        self._pending_repo = pending_repo
        self._config = config
        self._mod_approval = ModApprovalService(pending_repo, config=config)

    def apply_field_correction(
        self,
        *,
        parent_interaction_id: int,
        actor_discord_id: str,
        field: str,
        old: object,
        new: object,
        slot_index: int | None = None,
    ) -> ModApprovalNeedsConfirmation | None:
        parent = self._require_open_extraction(parent_interaction_id)
        entry = _correction_entry(
            field=field,
            old=old,
            new=new,
            slot_index=slot_index,
        )

        if is_bot_mod(actor_discord_id, self._config):
            apply_field_correction_to_parent(
                self._pending_repo,
                parent_interaction_id,
                field=field,
                new=new,
                slot_index=slot_index,
            )
            parent = self._require_open_extraction(parent_interaction_id)
            _append_parent_correction(self._pending_repo, parent, entry)
            logger.info(
                "mod field correction applied parent=%s field=%s old=%r new=%r",
                parent_interaction_id,
                field,
                old,
                new,
            )
            return None

        _queue_in_correction_session(self._pending_repo, parent, entry)
        logger.info(
            "queued field correction for mod approval parent=%s field=%s old=%r new=%r",
            parent_interaction_id,
            field,
            old,
            new,
        )
        return None

    def submit_for_approval(
        self,
        *,
        parent_interaction_id: int,
        uploader_discord_id: str,
    ) -> ModApprovalNeedsConfirmation:
        return self._mod_approval.submit_for_approval(
            parent_interaction_id=parent_interaction_id,
            uploader_discord_id=uploader_discord_id,
        )

    def _require_open_extraction(self, interaction_id: int) -> PendingInteraction:
        pending = self._pending_repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _CONFIRM_EXTRACTION_KIND:
            msg = f"Missing confirm_extraction pending: {interaction_id}"
            raise ValueError(msg)
        if pending.status != "open":
            msg = f"confirm_extraction pending is not open: {interaction_id}"
            raise ValueError(msg)
        extraction = pending.payload.get("extraction")
        if not isinstance(extraction, dict):
            msg = "Missing extraction payload"
            raise ValueError(msg)
        return pending
