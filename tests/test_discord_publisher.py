"""Tests for Discord report publisher (Task 012)."""

from __future__ import annotations

from unittest.mock import MagicMock

from scoretopia.discord.publisher import DiscordReportPublisher
from scoretopia.reports.dto import ReportDTO, ReportField


def test_discord_report_publisher_posts_embed_to_resolved_channel() -> None:
    reports_channel = MagicMock()
    reports_channel.send = MagicMock()
    channel_lookup = {"reports": reports_channel, "input": MagicMock()}

    publisher = DiscordReportPublisher(channel_lookup=channel_lookup)
    dto = ReportDTO(
        title="Active Games",
        description="1 game(s) currently in progress.",
        fields=[ReportField(label="Friday Night", value="Alice, Bob")],
    )

    publisher.publish("active_games", dto, channel_key="reports")

    reports_channel.send.assert_called_once()
    sent_embed = reports_channel.send.call_args.kwargs["embed"]
    assert sent_embed.title == "Active Games"
    assert sent_embed.description == "1 game(s) currently in progress."
    assert sent_embed.fields[0].name == "Friday Night"
    assert sent_embed.fields[0].value == "Alice, Bob"
