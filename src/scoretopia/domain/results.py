"""Typed result objects for domain operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from scoretopia.storage.models import Player


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
