"""Shared helpers for formatting game metadata in reports."""

from __future__ import annotations

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
