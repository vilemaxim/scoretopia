"""Discord embed builder for Scoretopia report posts."""

from __future__ import annotations

from datetime import UTC, datetime

import discord

from scoretopia.reports.dto import ReportDTO, ReportField
from scoretopia.reports.kinds import ReportKind

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


def embed_from_report_dto(dto: ReportDTO) -> discord.Embed:
    kind = dto.kind or ReportKind.active_games
    return build_embed(
        kind,
        title=dto.title,
        description=dto.description,
        fields=dto.fields,
        footer=dto.footer,
    )
