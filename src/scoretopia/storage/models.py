"""Database row dataclasses for Scoretopia storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def normalize_polytopia_name(name: str) -> str:
    """Normalize a Polytopia name for lookup (lowercase, stripped)."""
    return name.strip().lower()


@dataclass(frozen=True)
class Player:
    id: int
    polytopia_name: str
    discord_user_id: str | None
    discord_display_name: str | None


@dataclass(frozen=True)
class Game:
    id: int
    name: str
    status: str
    map_size: int | None
    terrain: str | None
    game_type: str | None
    target_score: int | None
    game_timer: str | None
    winner_player_id: int | None
    created_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class GameParticipantInput:
    player_id: int
    tribe: str | None = None
    score: int | None = None
    placement: int | None = None
    is_bot: bool = False


@dataclass(frozen=True)
class PlayerPairRatio:
    player_a_id: int
    player_b_id: int
    wins: int
    source: str
    updated_at: datetime | None = None
