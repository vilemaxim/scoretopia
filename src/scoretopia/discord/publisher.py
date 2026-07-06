"""Discord report publisher for scheduled and on-demand reports."""

from __future__ import annotations

import asyncio
from typing import Any

import discord

from scoretopia.reports.dto import ReportDTO


class DiscordReportPublisher:
    """Publish :class:`ReportDTO` payloads as Discord embeds."""

    def __init__(
        self,
        *,
        channel_lookup: dict[str, Any],
        bot: discord.Client | None = None,
    ) -> None:
        self._channel_lookup = channel_lookup
        self._bot = bot

    def publish(self, report_name: str, dto: ReportDTO, channel_key: str) -> None:
        del report_name
        channel = self._channel_lookup[channel_key]
        embed = _report_to_embed(dto)
        send_result = channel.send(embed=embed)
        if asyncio.iscoroutine(send_result):
            if self._bot is None:
                raise RuntimeError("Discord bot required for async channel.send")
            self._bot.loop.create_task(send_result)


def report_to_embed(dto: ReportDTO) -> discord.Embed:
    return _report_to_embed(dto)


def _report_to_embed(dto: ReportDTO) -> discord.Embed:
    embed = discord.Embed(title=dto.title, description=dto.description)
    for field in dto.fields:
        embed.add_field(name=field.label, value=field.value, inline=False)
    if dto.footer:
        embed.set_footer(text=dto.footer)
    return embed
