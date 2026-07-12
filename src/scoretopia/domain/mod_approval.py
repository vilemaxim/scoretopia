"""Bot-mod authorization and correction-session batching for ingest."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from scoretopia.config import is_bot_mod
from scoretopia.domain.actions import ModApprovalNeedsConfirmation
from scoretopia.storage.models import PendingInteraction
from scoretopia.storage.repos import PendingInteractionRepo, PlayerRepo

logger = logging.getLogger(__name__)

_CONFIRM_EXTRACTION_KIND = "confirm_extraction"
_MOD_APPROVAL_KIND = "mod_approval"


class _HasBotMods(Protocol):
    @property
    def bot_mods(self) -> object: ...


def _correction_entry(
    *,
    slot_index: int,
    old_name: str,
    new_name: str,
) -> dict[str, object]:
    return {
        "kind": "name_correction",
        "slot_index": slot_index,
        "old_name": old_name,
        "new_name": new_name,
    }


def _session_corrections(payload: dict[str, object]) -> list[dict[str, object]]:
    session = payload.get("correction_session")
    if isinstance(session, dict):
        corrections = session.get("corrections")
        if isinstance(corrections, list):
            return [entry for entry in corrections if isinstance(entry, dict)]
    corrections = payload.get("corrections")
    if isinstance(corrections, list):
        return [entry for entry in corrections if isinstance(entry, dict)]
    return []


def _summary_from_corrections(corrections: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for entry in corrections:
        if "old_name" in entry and "new_name" in entry:
            parts.append(f"{entry['old_name']} → {entry['new_name']}")
        elif "field" in entry and "old" in entry and "new" in entry:
            parts.append(f"{entry['field']}: {entry['old']} → {entry['new']}")
    return "; ".join(parts) if parts else "No corrections"


def _apply_name_correction_to_parent(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
    *,
    slot_index: int,
    new_name: str,
) -> None:
    parent = pending_repo.get_by_id(parent_id)
    if parent is None or parent.kind != _CONFIRM_EXTRACTION_KIND:
        msg = f"Missing confirm_extraction pending: {parent_id}"
        raise ValueError(msg)
    extraction = parent.payload.get("extraction")
    if not isinstance(extraction, dict):
        msg = "Missing extraction payload"
        raise ValueError(msg)
    players = extraction.get("players")
    if not isinstance(players, list):
        msg = "Missing extraction players"
        raise ValueError(msg)
    if slot_index < 0 or slot_index >= len(players):
        msg = f"Unknown player slot: {slot_index}"
        raise ValueError(msg)
    entry = players[slot_index]
    if not isinstance(entry, dict):
        msg = f"Invalid player entry at slot {slot_index}"
        raise ValueError(msg)
    entry["name"] = new_name
    pending_repo.update_payload(parent_id, parent.payload)


def _clear_parent_correction_session(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
) -> None:
    parent = pending_repo.get_by_id(parent_id)
    if parent is None:
        return
    if isinstance(parent.payload.get("correction_session"), dict):
        parent.payload["correction_session"] = {"corrections": []}
        pending_repo.update_payload(parent_id, parent.payload)


def _record_mod_approval(
    pending_repo: PendingInteractionRepo,
    parent_id: int,
    *,
    mod_discord_id: str,
) -> None:
    parent = pending_repo.get_by_id(parent_id)
    if parent is None:
        return
    approvals = parent.payload.get("mod_approvals")
    if not isinstance(approvals, list):
        approvals = []
    approvals.append(
        {
            "mod_discord_id": mod_discord_id,
            "approved_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    parent.payload["mod_approvals"] = approvals
    pending_repo.update_payload(parent_id, parent.payload)


class ModApprovalService:
    def __init__(
        self,
        pending_repo: PendingInteractionRepo,
        *,
        config: _HasBotMods,
        player_repo: PlayerRepo | None = None,
    ) -> None:
        self._pending_repo = pending_repo
        self._config = config
        # Reserved for sensitive new-player creation gating (task 030+).
        self._player_repo = player_repo

    def queue_name_correction(
        self,
        *,
        parent_interaction_id: int,
        actor_discord_id: str,
        slot_index: int,
        old_name: str,
        new_name: str,
    ) -> ModApprovalNeedsConfirmation | None:
        parent = self._require_open_extraction(parent_interaction_id)
        correction = _correction_entry(
            slot_index=slot_index,
            old_name=old_name,
            new_name=new_name,
        )

        if is_bot_mod(actor_discord_id, self._config):
            _apply_name_correction_to_parent(
                self._pending_repo,
                parent_interaction_id,
                slot_index=slot_index,
                new_name=new_name,
            )
            logger.info(
                "mod name correction applied immediately parent=%s slot=%s %s -> %s",
                parent_interaction_id,
                slot_index,
                old_name,
                new_name,
            )
            return None

        session = parent.payload.get("correction_session")
        if not isinstance(session, dict):
            session = {"corrections": []}
        corrections = session.get("corrections")
        if not isinstance(corrections, list):
            corrections = []
        corrections.append(correction)
        session["corrections"] = corrections
        parent.payload["correction_session"] = session
        self._pending_repo.update_payload(parent_interaction_id, parent.payload)
        logger.info(
            "queued name correction for mod approval parent=%s slot=%s %s -> %s",
            parent_interaction_id,
            slot_index,
            old_name,
            new_name,
        )
        return None

    def submit_for_approval(
        self,
        *,
        parent_interaction_id: int,
        uploader_discord_id: str,
    ) -> ModApprovalNeedsConfirmation:
        parent = self._require_open_extraction(parent_interaction_id)
        if parent.discord_user_id != uploader_discord_id:
            msg = "Only the uploader may submit a correction session"
            raise ValueError(msg)

        corrections = _session_corrections(parent.payload)
        if not corrections:
            msg = "No queued corrections to submit"
            raise ValueError(msg)

        summary = _summary_from_corrections(corrections)
        payload: dict[str, object] = {
            "parent_extraction_interaction_id": parent_interaction_id,
            "uploader_discord_id": uploader_discord_id,
            "correction_session": {"corrections": corrections},
            "corrections": corrections,
            "summary": summary,
        }
        pending = self._pending_repo.create(
            kind=_MOD_APPROVAL_KIND,
            discord_user_id=uploader_discord_id,
            payload=payload,
        )
        logger.info(
            "mod_approval pending created id=%s parent=%s corrections=%s",
            pending.id,
            parent_interaction_id,
            len(corrections),
        )
        return ModApprovalNeedsConfirmation(
            interaction_id=pending.id,
            parent_extraction_interaction_id=parent_interaction_id,
            summary=summary,
        )

    def approve(
        self,
        interaction_id: int,
        *,
        approver_discord_id: str,
    ) -> None:
        pending = self._require_mod_actor(interaction_id, approver_discord_id)
        parent_id = pending.payload.get("parent_extraction_interaction_id")
        if not isinstance(parent_id, int):
            msg = "mod_approval missing parent_extraction_interaction_id"
            raise ValueError(msg)

        # Lazy import avoids cycle with field_correction → ModApprovalService.
        from scoretopia.domain.field_correction import (
            apply_field_correction_to_parent,
        )

        for entry in _session_corrections(pending.payload):
            self._apply_correction_entry(
                parent_id,
                entry,
                apply_field=apply_field_correction_to_parent,
            )

        _record_mod_approval(
            self._pending_repo,
            parent_id,
            mod_discord_id=approver_discord_id,
        )
        _clear_parent_correction_session(self._pending_repo, parent_id)
        self._pending_repo.resolve(interaction_id)
        logger.info(
            "mod_approval approved id=%s by=%s parent=%s",
            interaction_id,
            approver_discord_id,
            parent_id,
        )

    def _apply_correction_entry(
        self,
        parent_id: int,
        entry: dict[str, object],
        *,
        apply_field: object,
    ) -> None:
        kind = entry.get("kind")
        if kind == "field_correction" or (
            "field" in entry and kind != "name_correction"
        ):
            field = entry.get("field")
            if not isinstance(field, str):
                return
            slot_index = entry.get("slot_index")
            slot = slot_index if isinstance(slot_index, int) else None
            apply_field(
                self._pending_repo,
                parent_id,
                field=field,
                new=entry.get("new"),
                slot_index=slot,
            )
            return

        slot_index = entry.get("slot_index")
        new_name = entry.get("new_name")
        if not isinstance(slot_index, int) or not isinstance(new_name, str):
            return
        _apply_name_correction_to_parent(
            self._pending_repo,
            parent_id,
            slot_index=slot_index,
            new_name=new_name,
        )

    def reject(
        self,
        interaction_id: int,
        *,
        rejector_discord_id: str,
    ) -> None:
        pending = self._require_mod_actor(interaction_id, rejector_discord_id)
        parent_id = pending.payload.get("parent_extraction_interaction_id")
        self._pending_repo.resolve(interaction_id)
        logger.info(
            "mod_approval rejected id=%s by=%s parent=%s (parent left open)",
            interaction_id,
            rejector_discord_id,
            parent_id,
        )

    def _require_mod_actor(
        self,
        interaction_id: int,
        actor_discord_id: str,
    ) -> PendingInteraction:
        pending = self._require_open_mod_approval(interaction_id)
        if not is_bot_mod(actor_discord_id, self._config):
            msg = "Only bot mods may approve or reject a correction batch"
            raise PermissionError(msg)
        return pending

    def _require_open_extraction(self, interaction_id: int) -> PendingInteraction:
        pending = self._pending_repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _CONFIRM_EXTRACTION_KIND:
            msg = f"Missing confirm_extraction pending: {interaction_id}"
            raise ValueError(msg)
        if pending.status != "open":
            msg = f"confirm_extraction pending is not open: {interaction_id}"
            raise ValueError(msg)
        return pending

    def _require_open_mod_approval(self, interaction_id: int) -> PendingInteraction:
        pending = self._pending_repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _MOD_APPROVAL_KIND:
            msg = f"Missing mod_approval pending: {interaction_id}"
            raise ValueError(msg)
        if pending.status != "open":
            msg = f"mod_approval pending is not open: {interaction_id}"
            raise ValueError(msg)
        return pending
