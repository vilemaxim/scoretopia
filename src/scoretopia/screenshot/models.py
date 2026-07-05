"""Structured data extracted from Polytopia screenshots."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GameEndHeader:
    score: int | None = None
    stars: int | None = None
    stars_gained: int | None = None
    turn: int | None = None


@dataclass(frozen=True)
class GameEndPlayer:
    name: str
    tribe: str | None = None
    status: str | None = None
    score: int | None = None
    elo_change: int | None = None
    elo: int | None = None
    is_winner: bool = False


@dataclass(frozen=True)
class GameEndExtraction:
    screenshot_type: str = "game_end"
    winner: str | None = None
    header: GameEndHeader = field(default_factory=GameEndHeader)
    players: tuple[GameEndPlayer, ...] = ()


@dataclass(frozen=True)
class WinRatio:
    you_name: str | None = None
    you_wins: int | None = None
    friend_name: str | None = None
    friend_wins: int | None = None


@dataclass(frozen=True)
class FriendProfileExtraction:
    screenshot_type: str = "friend_profile"
    friend_name: str | None = None
    alias: str | None = None
    num_friends: int | None = None
    games_played: int | None = None
    game_version: int | None = None
    elo: int | None = None
    win_ratio: WinRatio = field(default_factory=WinRatio)


@dataclass(frozen=True)
class GameBasicsPlayer:
    name: str
    is_you: bool = False
    is_eliminated: bool = False


@dataclass(frozen=True)
class GameBasicsExtraction:
    screenshot_type: str = "game_basics"
    game_name: str | None = None
    map_size: int | None = None
    terrain: str | None = None
    target_score: int | None = None
    game_type: str | None = None
    game_timer: str | None = None
    win_condition_text: str | None = None
    turn_status: str | None = None
    players: tuple[GameBasicsPlayer, ...] = ()


ExtractionResult = GameEndExtraction | FriendProfileExtraction | GameBasicsExtraction
