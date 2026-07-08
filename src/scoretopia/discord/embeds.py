"""Unified Discord embed builder for Scoretopia reports and lifecycle events."""

from __future__ import annotations

from datetime import UTC, datetime

import discord

from scoretopia.domain.actions import ActiveGameReport, ExtractionPreview
from scoretopia.reports.dto import ReportDTO, ReportField
from scoretopia.reports.game_settings import settings_summary
from scoretopia.reports.kinds import ReportKind
from scoretopia.screenshot.extract import format_extraction
from scoretopia.screenshot.models import (
    ExtractionResult,
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameEndExtraction,
)
from scoretopia.storage.models import Game

_SCREENSHOT_TYPE_TITLES = {
    "game_basics": "Game Basics",
    "game_end": "Game End",
    "friend_profile": "Friend Profile",
}
_MAX_EMBED_FIELDS = 25

_COLOUR_GAME_STARTED = 0x57F287
_COLOUR_GAME_COMPLETED = 0xFEE75C
_COLOUR_REPORT = 0x5865F2
_COLOUR_DISPUTE = 0xED4245


def colour_for_kind(kind: ReportKind) -> int:
    if kind == ReportKind.game_started:
        return _COLOUR_GAME_STARTED
    if kind == ReportKind.game_completed:
        return _COLOUR_GAME_COMPLETED
    if kind == ReportKind.dispute:
        return _COLOUR_DISPUTE
    return _COLOUR_REPORT


def participant_fields(
    *,
    human_player_names: tuple[str, ...],
    bot_count: int,
) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if human_player_names:
        fields.append(("Players", ", ".join(human_player_names)))
    if bot_count > 0:
        fields.append(("Bots", str(bot_count)))
    return fields


def build_embed(
    kind: ReportKind,
    *,
    title: str,
    description: str | None = None,
    fields: list[tuple[str, str]] | list[ReportField] | None = None,
    footer: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        colour=colour_for_kind(kind),
        timestamp=datetime.now(tz=UTC),
    )
    for field in fields or []:
        if isinstance(field, ReportField):
            embed.add_field(name=field.label, value=field.value, inline=False)
        else:
            name, value = field
            embed.add_field(name=name, value=value, inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


def build_game_started_embed(game: Game, report: ActiveGameReport) -> discord.Embed:
    return build_embed(
        ReportKind.game_started,
        title=f"Game started: {report.game_name}",
        description=settings_summary(game) or None,
        fields=participant_fields(
            human_player_names=report.human_player_names,
            bot_count=report.bot_count,
        ),
    )


def build_game_completed_embed(
    game_name: str,
    *,
    winner_name: str | None = None,
) -> discord.Embed:
    fields: list[tuple[str, str]] = []
    if winner_name:
        fields.append(("Winner", winner_name))
    return build_embed(
        ReportKind.game_completed,
        title=f"Game completed: {game_name}",
        fields=fields,
    )


def build_dispute_embed(body: str) -> discord.Embed:
    return build_embed(
        ReportKind.dispute,
        title="Win-ratio dispute",
        description=body,
    )


def _extraction_preview_fields(
    preview: ExtractionPreview,
    extraction: ExtractionResult | None,
) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if isinstance(extraction, GameBasicsExtraction):
        if extraction.game_name:
            fields.append(("Game", extraction.game_name))
        elif preview.game_name:
            fields.append(("Game", preview.game_name))
        settings = settings_summary(
            Game(
                id=0,
                name=extraction.game_name or "Preview",
                status="active",
                map_size=extraction.map_size,
                terrain=extraction.terrain,
                game_type=extraction.game_type,
                target_score=extraction.target_score,
                game_timer=extraction.game_timer,
                winner_player_id=None,
            )
        )
        if settings:
            fields.append(("Settings", settings))
        player_names = ", ".join(player.name for player in extraction.players)
        if player_names:
            fields.append(("Players", player_names))
        return fields

    if isinstance(extraction, GameEndExtraction):
        if extraction.winner:
            fields.append(("Winner", extraction.winner))
        for player in extraction.players:
            parts = [player.name]
            if player.score is not None:
                parts.append(f"{player.score:,} pts")
            value = " · ".join(parts[1:]) if len(parts) > 1 else player.name
            fields.append((player.name, value))
        return fields

    if isinstance(extraction, FriendProfileExtraction):
        if extraction.friend_name:
            fields.append(("Friend", extraction.friend_name))
        ratio = extraction.win_ratio
        if ratio.you_name and ratio.friend_name:
            you_wins = ratio.you_wins if ratio.you_wins is not None else "?"
            friend_wins = ratio.friend_wins if ratio.friend_wins is not None else "?"
            fields.append(
                (
                    "Win ratio",
                    f"{ratio.you_name} {you_wins}–{friend_wins} {ratio.friend_name}",
                )
            )
        return fields

    if preview.game_name:
        fields.append(("Game", preview.game_name))
    return fields


def build_extraction_preview_embed(
    preview: ExtractionPreview,
    *,
    extraction: ExtractionResult | None = None,
) -> discord.Embed:
    title = _SCREENSHOT_TYPE_TITLES.get(
        preview.screenshot_type,
        preview.screenshot_type.replace("_", " ").title(),
    )
    fields = _extraction_preview_fields(preview, extraction)
    description: str | None = None
    if extraction is not None and len(fields) >= _MAX_EMBED_FIELDS:
        description = format_extraction(extraction)
        fields = fields[: _MAX_EMBED_FIELDS - 1]
    return build_embed(
        ReportKind.active_games,
        title=title,
        description=description,
        fields=fields,
    )


def embed_from_report_dto(dto: ReportDTO) -> discord.Embed:
    kind = dto.kind or ReportKind.active_games
    return build_embed(
        kind,
        title=dto.title,
        description=dto.description,
        fields=dto.fields,
        footer=dto.footer,
    )
