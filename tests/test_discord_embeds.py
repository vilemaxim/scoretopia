"""Tests for unified Discord report embed builder (Task 001)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from scoretopia.discord.embeds import (
    ReportKind,
    build_embed,
    colour_for_kind,
    embed_from_report_dto,
)
from scoretopia.reports.dto import ReportDTO, ReportField


@pytest.mark.parametrize(
    ("kind", "expected_colour"),
    [
        (ReportKind.game_started, 0x57F287),
        (ReportKind.game_completed, 0xFEE75C),
        (ReportKind.active_games, 0x5865F2),
        (ReportKind.recent_completions, 0x5865F2),
        (ReportKind.win_ratios, 0x5865F2),
        (ReportKind.dispute, 0xED4245),
    ],
)
def test_colour_for_kind_maps_report_types(
    kind: ReportKind,
    expected_colour: int,
) -> None:
    assert colour_for_kind(kind) == expected_colour


def test_build_embed_applies_kind_colour_title_and_timestamp() -> None:
    fixed_now = datetime(2026, 7, 6, 17, 30, tzinfo=UTC)
    with patch("scoretopia.discord.embeds.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.UTC = UTC

        embed = build_embed(
            ReportKind.active_games,
            title="Active Games",
            description="1 game(s) currently in progress.",
        )

    assert embed.title == "Active Games"
    assert embed.description == "1 game(s) currently in progress."
    assert embed.colour.value == 0x5865F2
    assert embed.timestamp == fixed_now


def test_build_embed_adds_fields_and_footer() -> None:
    embed = build_embed(
        ReportKind.win_ratios,
        title="Win Ratios",
        description="Head-to-head records.",
        fields=[
            ("Alice", "Bob: 5-3"),
            ("Bob", "Alice: 3-5"),
        ],
        footer="Updated just now",
    )

    assert len(embed.fields) == 2
    assert embed.fields[0].name == "Alice"
    assert embed.fields[0].value == "Bob: 5-3"
    assert embed.footer is not None
    assert embed.footer.text == "Updated just now"


def test_embed_from_report_dto_uses_kind_colour_and_timestamp() -> None:
    dto = ReportDTO(
        title="Recent Completions",
        description="1 game(s) completed in the last 7 day(s).",
        fields=[ReportField(label="Recent Win", value="Winner: Alice · Players: Bob")],
        kind=ReportKind.recent_completions,
    )

    embed = embed_from_report_dto(dto)

    assert embed.title == "Recent Completions"
    assert embed.colour.value == 0x5865F2
    assert embed.timestamp is not None
    assert embed.fields[0].name == "Recent Win"