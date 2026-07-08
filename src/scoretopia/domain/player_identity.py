"""Unknown-player identity verification during staged ingest."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from scoretopia.domain.actions import (
    PlayerLinkNeedsConfirmation,
    UnresolvedPlayerPreview,
)
from scoretopia.domain.matching import is_bot_name
from scoretopia.screenshot.models import ExtractionResult, GameBasicsExtraction
from scoretopia.storage.repos import PendingInteractionRepo, PlayerRepo

logger = logging.getLogger(__name__)

_CONFIRM_PLAYER_LINK_KIND = "confirm_player_link"


class ConfirmPlayerLinkOutcome(Enum):
    SUCCESS = "success"
    NOT_AUTHORIZED = "not_authorized"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ConfirmPlayerLinkResult:
    outcome: ConfirmPlayerLinkOutcome
    blocked_owner_discord_id: str | None = None


def _slot_payload(unresolved: UnresolvedPlayerPreview) -> dict[str, object]:
    return {
        "slot_index": unresolved.slot_index,
        "polytopia_name": unresolved.polytopia_name,
        "player_id": unresolved.player_id,
        "spelling_confirmed": False,
        "selected_discord_user_id": None,
        "resolved": False,
    }


def _unresolved_from_slots(
    slots: list[dict[str, object]],
) -> tuple[UnresolvedPlayerPreview, ...]:
    unresolved: list[UnresolvedPlayerPreview] = []
    for slot in slots:
        if slot.get("resolved"):
            continue
        player_id = slot.get("player_id")
        unresolved.append(
            UnresolvedPlayerPreview(
                slot_index=int(slot["slot_index"]),
                polytopia_name=str(slot["polytopia_name"]),
                player_id=int(player_id) if isinstance(player_id, int) else None,
            )
        )
    return tuple(unresolved)


class PlayerIdentityService:
    def __init__(
        self,
        player_repo: PlayerRepo,
        pending_repo: PendingInteractionRepo,
    ) -> None:
        self._player_repo = player_repo
        self._pending_repo = pending_repo

    def list_unresolved_humans(
        self,
        extraction: ExtractionResult,
    ) -> list[UnresolvedPlayerPreview]:
        if not isinstance(extraction, GameBasicsExtraction):
            return []

        unresolved: list[UnresolvedPlayerPreview] = []
        for slot_index, player in enumerate(extraction.players):
            if is_bot_name(player.name):
                continue
            existing = self._player_repo.get_by_polytopia_name(player.name)
            if existing is not None and existing.discord_user_id is not None:
                continue
            unresolved.append(
                UnresolvedPlayerPreview(
                    slot_index=slot_index,
                    polytopia_name=player.name,
                    player_id=existing.id if existing is not None else None,
                )
            )
        return unresolved

    def find_pending_for_parent(
        self,
        parent_interaction_id: int,
    ) -> PlayerLinkNeedsConfirmation | None:
        for pending in self._pending_repo.list_open_by_kind(_CONFIRM_PLAYER_LINK_KIND):
            parent_id = pending.payload.get("parent_extraction_interaction_id")
            if parent_id == parent_interaction_id:
                slots = pending.payload.get("slots")
                slot_list = slots if isinstance(slots, list) else []
                return PlayerLinkNeedsConfirmation(
                    interaction_id=pending.id,
                    parent_extraction_interaction_id=parent_interaction_id,
                    unresolved=_unresolved_from_slots(slot_list),
                )
        return None

    def begin_identity_check(
        self,
        *,
        parent_interaction_id: int,
        uploader_discord_id: str,
        extraction: ExtractionResult,
        unresolved: list[UnresolvedPlayerPreview],
    ) -> PlayerLinkNeedsConfirmation:
        payload: dict[str, object] = {
            "parent_extraction_interaction_id": parent_interaction_id,
            "uploader_discord_id": uploader_discord_id,
            "screenshot_type": getattr(extraction, "screenshot_type", None),
            "slots": [_slot_payload(entry) for entry in unresolved],
        }
        pending = self._pending_repo.create(
            kind=_CONFIRM_PLAYER_LINK_KIND,
            discord_user_id=uploader_discord_id,
            payload=payload,
        )
        return PlayerLinkNeedsConfirmation(
            interaction_id=pending.id,
            parent_extraction_interaction_id=parent_interaction_id,
            unresolved=tuple(unresolved),
        )

    def confirm_spelling(
        self,
        interaction_id: int,
        *,
        slot_index: int,
        confirmer_discord_id: str,
    ) -> None:
        pending = self._require_open_pending(interaction_id)
        if pending.discord_user_id != confirmer_discord_id:
            return
        slot = self._slot_for_index(pending.payload, slot_index)
        slot["spelling_confirmed"] = True
        self._save_slots(interaction_id, pending.payload)

    def reject_spelling(
        self,
        interaction_id: int,
        *,
        slot_index: int,
        confirmer_discord_id: str,
    ) -> None:
        pending = self._require_open_pending(interaction_id)
        if pending.discord_user_id != confirmer_discord_id:
            return
        slot = self._slot_for_index(pending.payload, slot_index)
        slot["spelling_confirmed"] = False
        self._save_slots(interaction_id, pending.payload)

    def pick_canonical_player(
        self,
        interaction_id: int,
        *,
        slot_index: int,
        player_id: int,
        picker_discord_id: str,
    ) -> None:
        pending = self._require_open_pending(interaction_id)
        if pending.discord_user_id != picker_discord_id:
            return
        player = self._player_repo.get_by_id(player_id)
        if player is None or is_bot_name(player.polytopia_name):
            msg = f"Unknown human player: {player_id}"
            raise ValueError(msg)

        slot = self._slot_for_index(pending.payload, slot_index)
        old_name = str(slot["polytopia_name"])
        slot["polytopia_name"] = player.polytopia_name
        slot["player_id"] = player.id
        slot["spelling_confirmed"] = True
        if player.discord_user_id is not None:
            slot["selected_discord_user_id"] = player.discord_user_id
        self._save_slots(interaction_id, pending.payload)
        self._update_parent_extraction_player_name(
            pending.payload,
            slot_index=slot_index,
            canonical_name=player.polytopia_name,
        )
        logger.info(
            "player correction picked: %s -> %s by %s",
            old_name,
            player.polytopia_name,
            picker_discord_id,
        )

    def select_discord_user(
        self,
        interaction_id: int,
        *,
        slot_index: int,
        selected_discord_user_id: str,
        confirmer_discord_id: str,
    ) -> None:
        pending = self._require_open_pending(interaction_id)
        if pending.discord_user_id != confirmer_discord_id:
            return
        slot = self._slot_for_index(pending.payload, slot_index)
        slot["selected_discord_user_id"] = selected_discord_user_id
        self._save_slots(interaction_id, pending.payload)

    def confirm_remote_link(
        self,
        interaction_id: int,
        *,
        slot_index: int,
        confirmer_discord_id: str,
    ) -> ConfirmPlayerLinkResult:
        pending = self._require_open_pending(interaction_id)
        slot = self._slot_for_index(pending.payload, slot_index)
        selected = slot.get("selected_discord_user_id")
        if not isinstance(selected, str) or selected != confirmer_discord_id:
            return ConfirmPlayerLinkResult(
                outcome=ConfirmPlayerLinkOutcome.NOT_AUTHORIZED,
            )

        polytopia_name = str(slot["polytopia_name"])
        existing = self._player_repo.get_by_polytopia_name(polytopia_name)
        if self._polytopia_claimed_by_other(existing, confirmer_discord_id):
            owner_id = existing.discord_user_id if existing is not None else None
            return ConfirmPlayerLinkResult(
                outcome=ConfirmPlayerLinkOutcome.BLOCKED,
                blocked_owner_discord_id=owner_id,
            )

        player = self._link_player(
            polytopia_name=polytopia_name,
            player_id=slot.get("player_id"),
            existing=existing,
            discord_user_id=confirmer_discord_id,
        )
        slot["player_id"] = player.id
        slot["resolved"] = True
        self._save_slots(interaction_id, pending.payload)

        slots = pending.payload.get("slots")
        slot_list = slots if isinstance(slots, list) else []
        if all(bool(entry.get("resolved")) for entry in slot_list):
            self._pending_repo.resolve(interaction_id)

        return ConfirmPlayerLinkResult(outcome=ConfirmPlayerLinkOutcome.SUCCESS)

    def _link_player(
        self,
        *,
        polytopia_name: str,
        player_id: object,
        existing,
        discord_user_id: str,
    ):
        if isinstance(player_id, int):
            return self._player_repo.update_discord_link(
                player_id,
                discord_user_id=discord_user_id,
                discord_display_name=None,
            )
        if existing is not None:
            return self._player_repo.update_discord_link(
                existing.id,
                discord_user_id=discord_user_id,
                discord_display_name=None,
            )
        return self._player_repo.create(
            polytopia_name=polytopia_name,
            discord_user_id=discord_user_id,
        )

    def _require_open_pending(self, interaction_id: int):
        pending = self._pending_repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _CONFIRM_PLAYER_LINK_KIND:
            msg = f"Missing player-link pending interaction: {interaction_id}"
            raise ValueError(msg)
        if pending.status != "open":
            msg = f"Player-link pending interaction is not open: {interaction_id}"
            raise ValueError(msg)
        return pending

    def _slot_for_index(
        self,
        payload: dict[str, object],
        slot_index: int,
    ) -> dict[str, object]:
        slots = payload.get("slots")
        if not isinstance(slots, list):
            msg = "Missing player-link slot payload"
            raise ValueError(msg)
        for slot in slots:
            if isinstance(slot, dict) and slot.get("slot_index") == slot_index:
                return slot
        msg = f"Unknown player slot: {slot_index}"
        raise ValueError(msg)

    def _save_slots(self, interaction_id: int, payload: dict[str, object]) -> None:
        self._pending_repo.update_payload(interaction_id, payload)

    def _update_parent_extraction_player_name(
        self,
        payload: dict[str, object],
        *,
        slot_index: int,
        canonical_name: str,
    ) -> None:
        parent_id = payload.get("parent_extraction_interaction_id")
        if not isinstance(parent_id, int):
            return
        parent = self._pending_repo.get_by_id(parent_id)
        if parent is None:
            return
        extraction = parent.payload.get("extraction")
        if not isinstance(extraction, dict):
            return
        players = extraction.get("players")
        if not isinstance(players, list):
            return
        if slot_index >= len(players):
            return
        entry = players[slot_index]
        if not isinstance(entry, dict):
            return
        entry["name"] = canonical_name
        self._pending_repo.update_payload(parent_id, parent.payload)

    def _polytopia_claimed_by_other(
        self,
        player,
        discord_user_id: str,
    ) -> bool:
        return (
            player is not None
            and player.discord_user_id is not None
            and player.discord_user_id != discord_user_id
        )
