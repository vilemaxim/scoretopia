"""Shared helpers for formatting game metadata in reports."""

from __future__ import annotations

from datetime import UTC

from scoretopia.storage.models import Game

_FIELD_SEP = " · "


def settings_summary(game: Game) -> str:
    parts: list[str] = []
    if game.terrain:
        parts.append(game.terrain)
    if game.map_size is not None:
        parts.append(str(game.map_size))
    if game.game_type:
        parts.append(game.game_type)
    if game.target_score is not None:
        parts.append(f"score {game.target_score}")
    if game.game_timer:
        parts.append(game.game_timer)
    return _FIELD_SEP.join(parts)


def _format_started_date(game: Game) -> str:
    if game.created_at is None:
        return "Started unknown"
    value = game.created_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return f"Started {value.strftime('%Y-%m-%d')}"


def active_game_stats_line(game: Game) -> str:
    """Stats line for active games embeds/CLI; uses placeholders for missing fields."""
    parts = [
        _format_started_date(game),
        game.terrain or "terrain unknown",
        str(game.map_size) if game.map_size is not None else "size unknown",
        game.game_type or "mode unknown",
        (
            f"score {game.target_score}"
            if game.target_score is not None
            else "score unknown"
        ),
        game.game_timer or "timer unknown",
    ]
    return _FIELD_SEP.join(parts)
