"""Report type identifiers shared across platform layers."""

from __future__ import annotations

from enum import Enum


class ReportKind(Enum):
    game_started = "game_started"
    game_completed = "game_completed"
    active_games = "active_games"
    recent_completions = "recent_completions"
    win_ratios = "win_ratios"
    dispute = "dispute"
