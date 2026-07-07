"""Tests for Discord report publisher (Task 012)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scoretopia.discord.embeds import ReportKind
from scoretopia.discord.publisher import DiscordReportPublisher, report_to_embed
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
        kind=ReportKind.active_games,
    )

    publisher.publish("active_games", dto, channel_key="reports")

    reports_channel.send.assert_called_once()
    sent_embed = reports_channel.send.call_args.kwargs["embed"]
    assert sent_embed.title == "Active Games"
    assert sent_embed.description == "1 game(s) currently in progress."
    assert sent_embed.fields[0].name == "Friday Night"
    assert sent_embed.fields[0].value == "Alice, Bob"
    assert sent_embed.colour.value == 0x5865F2
    assert sent_embed.timestamp is not None


@pytest.mark.parametrize(
    ("kind", "expected_colour"),
    [
        (ReportKind.active_games, 0x5865F2),
        (ReportKind.recent_completions, 0x5865F2),
        (ReportKind.win_ratios, 0x5865F2),
    ],
)
def test_report_to_embed_applies_kind_colour_and_timestamp(
    kind: ReportKind,
    expected_colour: int,
) -> None:
    dto = ReportDTO(
        title="Report",
        description="Summary",
        fields=[ReportField(label="Row", value="Details")],
        kind=kind,
    )

    embed = report_to_embed(dto)

    assert embed.colour.value == expected_colour
    assert embed.timestamp is not None
