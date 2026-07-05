"""Typed result objects for domain operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from scoretopia.storage.models import Game, Player


class RegisterOutcome(Enum):
    SUCCESS = "success"
    ALREADY_LINKED_TO_OTHER = "already_linked_to_other"


@dataclass(frozen=True)
class RegisterResult:
    outcome: RegisterOutcome
    player: Player | None = None

    @classmethod
    def success(cls, player: Player) -> RegisterResult:
        return cls(outcome=RegisterOutcome.SUCCESS, player=player)

    @classmethod
    def already_linked_to_other(cls) -> RegisterResult:
        return cls(outcome=RegisterOutcome.ALREADY_LINKED_TO_OTHER, player=None)


class MatchOutcome(Enum):
    NONE = "none"
    ONE = "one"
    MANY = "many"


@dataclass(frozen=True)
class MatchResult:
    outcome: MatchOutcome
    games: tuple[Game, ...] = ()


@dataclass(frozen=True)
class CompleteResult:
    game: Game


@dataclass(frozen=True)
class RejectResult:
    interaction_id: int
