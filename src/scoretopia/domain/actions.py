"""Action DTOs returned by the ingest orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from scoretopia.storage.models import Game


@dataclass(frozen=True)
class ActiveGameReport:
    """Platform-agnostic payload for the active_games report channel."""

    game_id: int
    game_name: str
    player_names: tuple[str, ...]


@dataclass(frozen=True)
class GameStarted:
    game: Game
    report: ActiveGameReport
    action: str = "game_started"


@dataclass(frozen=True)
class GameEndNeedsConfirmation:
    game_id: int
    interaction_id: int
    action: str = "game_end_needs_confirmation"


@dataclass(frozen=True)
class GameEndNeedsPick:
    game_ids: tuple[int, ...]
    interaction_id: int
    action: str = "game_end_needs_pick"


@dataclass(frozen=True)
class GameEndPendingStart:
    interaction_id: int
    action: str = "game_end_pending_start"


@dataclass(frozen=True)
class WinRatioNeedsConfirmation:
    other_player_id: int
    interaction_id: int
    action: str = "win_ratio_needs_confirmation"


@dataclass(frozen=True)
class UnrecognizedScreenshot:
    message: str
    action: str = "unrecognized_screenshot"


@dataclass(frozen=True)
class IngestError:
    message: str
    action: str = "error"
    detail: str | None = None


IngestResult = (
    GameStarted
    | GameEndNeedsConfirmation
    | GameEndNeedsPick
    | GameEndPendingStart
    | WinRatioNeedsConfirmation
    | UnrecognizedScreenshot
    | IngestError
)
