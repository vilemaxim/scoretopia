"""Action DTOs returned by the ingest orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from scoretopia.storage.models import Game


@dataclass(frozen=True)
class ExtractionPreview:
    screenshot_type: str
    game_name: str | None = None


@dataclass(frozen=True)
class ExtractionNeedsConfirmation:
    interaction_id: int
    preview: ExtractionPreview
    action: str = "extraction_needs_confirmation"


@dataclass(frozen=True)
class StagedIngestNotAuthorized:
    action: str = "not_authorized"


@dataclass(frozen=True)
class ActiveGameReport:
    """Platform-agnostic payload for the active_games report channel."""

    game_id: int
    game_name: str
    human_player_names: tuple[str, ...]
    bot_count: int


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
    extracted_human_names: tuple[str, ...] = ()
    active_game_rosters: tuple[str, ...] = ()
    action: str = "game_end_pending_start"


@dataclass(frozen=True)
class WinRatioNeedsConfirmation:
    other_player_id: int
    interaction_id: int
    action: str = "win_ratio_needs_confirmation"


@dataclass(frozen=True)
class UnresolvedPlayerPreview:
    slot_index: int
    polytopia_name: str
    player_id: int | None = None


@dataclass(frozen=True)
class PlayerLinkNeedsConfirmation:
    interaction_id: int
    parent_extraction_interaction_id: int
    unresolved: tuple[UnresolvedPlayerPreview, ...]
    action: str = "player_link_needs_confirmation"


@dataclass(frozen=True)
class ModApprovalNeedsConfirmation:
    interaction_id: int
    parent_extraction_interaction_id: int
    summary: str
    action: str = "mod_approval_needs_confirmation"


@dataclass(frozen=True)
class FieldCorrectionNeedsInput:
    interaction_id: int
    parent_extraction_interaction_id: int
    screenshot_type: str
    action: str = "field_correction_needs_input"


@dataclass(frozen=True)
class RosterSlotsUnresolved:
    """continue_review rejected because fuzzy/new slots need Fix resolution."""

    unresolved_slot_indexes: tuple[int, ...] = ()
    action: str = "roster_slots_unresolved"


@dataclass(frozen=True)
class FinalSummaryPreview:
    screenshot_type: str
    game_name: str | None = None
    map_size: int | None = None
    terrain: str | None = None
    game_timer: str | None = None
    target_score: int | None = None
    game_type: str | None = None
    roster: tuple[str, ...] = ()
    winner: str | None = None
    scores: tuple[tuple[str, int | None], ...] = ()


@dataclass(frozen=True)
class FinalSummaryNeedsConfirmation:
    interaction_id: int
    parent_extraction_interaction_id: int
    summary: FinalSummaryPreview
    action: str = "final_summary_needs_confirmation"


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
    | PlayerLinkNeedsConfirmation
    | ModApprovalNeedsConfirmation
    | FieldCorrectionNeedsInput
    | RosterSlotsUnresolved
    | FinalSummaryNeedsConfirmation
    | UnrecognizedScreenshot
    | IngestError
)
