"""Discord report publisher for scheduled and on-demand reports."""

from __future__ import annotations

import asyncio
from typing import Any

import discord

from scoretopia.discord.embeds import embed_from_report_dto
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
        embed = report_to_embed(dto)
        send_result = channel.send(embed=embed)
        if asyncio.iscoroutine(send_result):
            if self._bot is None:
                raise RuntimeError("Discord bot required for async channel.send")
            self._bot.loop.create_task(send_result)


def report_to_embed(dto: ReportDTO) -> discord.Embed:
    return embed_from_report_dto(dto)
