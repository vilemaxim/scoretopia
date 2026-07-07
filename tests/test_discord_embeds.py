"""Tests for unified Discord report embed builder (Task 001)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from scoretopia.discord.embeds import (
    ReportKind,
    build_dispute_embed,
    build_embed,
    build_game_completed_embed,
    build_game_started_embed,
    colour_for_kind,
    embed_from_report_dto,
    participant_fields,
)
from scoretopia.domain.actions import ActiveGameReport
from scoretopia.reports.dto import ReportDTO, ReportField
from scoretopia.storage.models import Game


def _sample_game(**overrides: object) -> Game:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "Friday Night",
        "status": "active",
        "map_size": 12,
        "terrain": "Drylands",
        "game_type": "Domination",
        "target_score": 10000,
        "game_timer": "Blitz",
        "winner_player_id": None,
        "created_at": datetime(2026, 7, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Game(**defaults)  # type: ignore[arg-type]


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


def test_participant_fields_lists_humans_and_bot_count() -> None:
    fields = participant_fields(
        human_player_names=("Alice", "Bob"),
        bot_count=2,
    )

    assert fields == [
        ("Players", "Alice, Bob"),
        ("Bots", "2"),
    ]


def test_participant_fields_omits_bots_when_count_is_zero() -> None:
    fields = participant_fields(
        human_player_names=("Alice", "Bob"),
        bot_count=0,
    )

    assert fields == [("Players", "Alice, Bob")]


def test_build_game_started_embed_uses_colon_title_and_settings_description() -> None:
    game = _sample_game()
    report = ActiveGameReport(
        game_id=1,
        game_name="Friday Night",
        human_player_names=("Alice", "Bob"),
        bot_count=0,
    )

    embed = build_game_started_embed(game, report)

    assert embed.title == "Game started: Friday Night"
    assert embed.colour.value == 0x57F287
    assert embed.timestamp is not None
    assert "Drylands" in (embed.description or "")
    assert "12" in (embed.description or "")
    assert "Domination" in (embed.description or "")
    assert "score 10000" in (embed.description or "")
    assert "Blitz" in (embed.description or "")


def test_build_game_started_embed_separates_humans_and_bots() -> None:
    game = _sample_game(name="Bots Included")
    report = ActiveGameReport(
        game_id=2,
        game_name="Bots Included",
        human_player_names=("Alice",),
        bot_count=2,
    )

    embed = build_game_started_embed(game, report)
    field_map = {field.name: field.value for field in embed.fields}

    assert field_map["Players"] == "Alice"
    assert field_map["Bots"] == "2"


def test_build_game_started_embed_omits_bots_field_when_zero() -> None:
    game = _sample_game()
    report = ActiveGameReport(
        game_id=1,
        game_name="Friday Night",
        human_player_names=("Alice", "Bob"),
        bot_count=0,
    )

    embed = build_game_started_embed(game, report)
    field_names = [field.name for field in embed.fields]

    assert "Players" in field_names
    assert "Bots" not in field_names


def test_build_game_completed_embed_shows_winner_when_known() -> None:
    embed = build_game_completed_embed("Friday Night", winner_name="Alice")

    assert embed.title == "Game completed: Friday Night"
    assert embed.colour.value == 0xFEE75C
    assert embed.timestamp is not None
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Winner"] == "Alice"


def test_build_game_completed_embed_without_winner_omits_winner_field() -> None:
    embed = build_game_completed_embed("Friday Night")

    assert embed.title == "Game completed: Friday Night"
    field_names = [field.name for field in embed.fields]
    assert "Winner" not in field_names


def test_build_dispute_embed_uses_dispute_colour_and_timestamp() -> None:
    embed = build_dispute_embed("Win-ratio dispute: Alice claimed 9–11 vs Bob.")

    assert embed.title == "Win-ratio dispute"
    assert embed.description == "Win-ratio dispute: Alice claimed 9–11 vs Bob."
    assert embed.colour.value == 0xED4245
    assert embed.timestamp is not None


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
