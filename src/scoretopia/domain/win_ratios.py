"""Win-ratio screenshot confirmation and dispute handling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from scoretopia.domain.players import PlayerService
from scoretopia.screenshot.models import FriendProfileExtraction
from scoretopia.storage.models import DisputeCreate, PendingInteraction, Player
from scoretopia.storage.repos import (
    DisputeRepo,
    PendingInteractionRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)


class ConfirmOutcome(Enum):
    SUCCESS = "success"
    NOT_AUTHORIZED = "not_authorized"


@dataclass(frozen=True)
class PendingWinRatio:
    interaction_id: int
    other_player_id: int


@dataclass(frozen=True)
class ConfirmResult:
    outcome: ConfirmOutcome

    @classmethod
    def success(cls) -> ConfirmResult:
        return cls(outcome=ConfirmOutcome.SUCCESS)

    @classmethod
    def not_authorized(cls) -> ConfirmResult:
        return cls(outcome=ConfirmOutcome.NOT_AUTHORIZED)


@dataclass(frozen=True)
class DisputeResult:
    dispute_id: int
    message: str
    action: str = "win_ratio_disputed"


@dataclass(frozen=True)
class _PendingPair:
    submitter: Player
    other: Player
    you_wins: int
    friend_wins: int
    screenshot_path: str | None


class WinRatioService:
    def __init__(
        self,
        player_repo: PlayerRepo,
        pending_repo: PendingInteractionRepo,
        ratio_repo: PlayerPairRatioRepo,
        dispute_repo: DisputeRepo,
    ) -> None:
        self._player_repo = player_repo
        self._pending_repo = pending_repo
        self._ratio_repo = ratio_repo
        self._dispute_repo = dispute_repo
        self._player_service = PlayerService(player_repo)

    def submit_from_screenshot(
        self,
        extraction: FriendProfileExtraction,
        submitter_discord_id: str,
        *,
        screenshot_path: str | None = None,
    ) -> PendingWinRatio:
        friend_name = extraction.friend_name or extraction.win_ratio.friend_name
        assert friend_name is not None
        friend = self._player_service.resolve_or_create_polytopia_name(friend_name)

        payload: dict[str, object] = {
            "other_player_id": friend.id,
            "friend_name": friend_name,
            "you_wins": extraction.win_ratio.you_wins,
            "friend_wins": extraction.win_ratio.friend_wins,
        }
        if screenshot_path is not None:
            payload["screenshot_path"] = screenshot_path

        pending = self._pending_repo.create(
            kind="win_ratio_needs_confirmation",
            discord_user_id=submitter_discord_id,
            payload=payload,
        )
        return PendingWinRatio(
            interaction_id=pending.id,
            other_player_id=friend.id,
        )

    def confirm(
        self,
        interaction_id: int,
        confirmer_discord_id: str,
    ) -> ConfirmResult:
        pending = self._require_pending(interaction_id)
        if not self._is_other_player(pending, confirmer_discord_id):
            return ConfirmResult.not_authorized()

        pair = self._pair_from_pending(pending)
        self._apply_screenshot_ratios(pair)
        self._pending_repo.resolve(interaction_id)
        return ConfirmResult.success()

    def reject(
        self,
        interaction_id: int,
        confirmer_discord_id: str,
        reason: str | None = None,
    ) -> DisputeResult:
        pending = self._require_pending(interaction_id)
        pair = self._pair_from_pending(pending)
        rejector = self._player_repo.get_by_discord_id(confirmer_discord_id)
        assert rejector is not None

        dispute = self._dispute_repo.create(
            DisputeCreate(
                player_a_id=pair.submitter.id,
                player_b_id=pair.other.id,
                submitter_player_id=pair.submitter.id,
                rejector_player_id=rejector.id,
                claimed_wins_a=pair.you_wins,
                claimed_wins_b=pair.friend_wins,
                screenshot_path=pair.screenshot_path,
                status="open",
            )
        )
        self._pending_repo.mark_disputed(interaction_id)
        return DisputeResult(
            dispute_id=dispute.id,
            message=self._dispute_message(pair, reason),
        )

    def _require_pending(self, interaction_id: int) -> PendingInteraction:
        pending = self._pending_repo.get_by_id(interaction_id)
        assert pending is not None
        return pending

    def _pair_from_pending(self, pending: PendingInteraction) -> _PendingPair:
        screenshot_path = pending.payload.get("screenshot_path")
        path = str(screenshot_path) if screenshot_path is not None else None
        return _PendingPair(
            submitter=self._submitter_for_pending(pending),
            other=self._other_player_for_pending(pending),
            you_wins=int(pending.payload["you_wins"]),
            friend_wins=int(pending.payload["friend_wins"]),
            screenshot_path=path,
        )

    def _apply_screenshot_ratios(self, pair: _PendingPair) -> None:
        self._ratio_repo.upsert_ratio(
            pair.submitter.id,
            pair.other.id,
            wins=pair.you_wins,
            source="screenshot",
        )
        self._ratio_repo.upsert_ratio(
            pair.other.id,
            pair.submitter.id,
            wins=pair.friend_wins,
            source="screenshot",
        )

    def _dispute_message(self, pair: _PendingPair, reason: str | None) -> str:
        message = (
            f"Win-ratio dispute: {pair.submitter.polytopia_name} claimed "
            f"{pair.you_wins}–{pair.friend_wins} vs {pair.other.polytopia_name}."
        )
        if reason:
            message = f"{message} Reason: {reason}"
        return message

    def _submitter_for_pending(self, pending: PendingInteraction) -> Player:
        submitter = self._player_repo.get_by_discord_id(pending.discord_user_id)
        assert submitter is not None
        return submitter

    def _other_player_for_pending(self, pending: PendingInteraction) -> Player:
        other_player_id = int(pending.payload["other_player_id"])
        other = self._player_repo.get_by_id(other_player_id)
        assert other is not None
        return other

    def _is_other_player(
        self,
        pending: PendingInteraction,
        confirmer_discord_id: str,
    ) -> bool:
        other = self._other_player_for_pending(pending)
        return other.discord_user_id == confirmer_discord_id
