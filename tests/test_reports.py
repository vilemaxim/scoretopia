"""Tests for read-only report generators and text formatting (Task 010)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scoretopia.reports.dto import ReportDTO, ReportField
from scoretopia.reports.format import format_report_text
from scoretopia.reports.service import ReportService
from scoretopia.storage.db import open_database
from scoretopia.storage.models import GameParticipantInput
from scoretopia.storage.repos import (
    GameParticipantRepo,
    GameRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def player_repo(conn: sqlite3.Connection) -> PlayerRepo:
    return PlayerRepo(conn)


@pytest.fixture
def game_repo(conn: sqlite3.Connection) -> GameRepo:
    return GameRepo(conn)


@pytest.fixture
def participant_repo(conn: sqlite3.Connection) -> GameParticipantRepo:
    return GameParticipantRepo(conn)


@pytest.fixture
def ratio_repo(conn: sqlite3.Connection) -> PlayerPairRatioRepo:
    return PlayerPairRatioRepo(conn)


@pytest.fixture
def report_service(
    game_repo: GameRepo,
    participant_repo: GameParticipantRepo,
    player_repo: PlayerRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> ReportService:
    return ReportService(game_repo, participant_repo, player_repo, ratio_repo)


def _add_participants(
    participant_repo: GameParticipantRepo,
    game_id: int,
    *participants: GameParticipantInput,
) -> None:
    participant_repo.add_participants(game_id, participants)


def _add_human_participants(
    participant_repo: GameParticipantRepo,
    game_id: int,
    *player_ids: int,
) -> None:
    participants = [
        GameParticipantInput(player_id=player_id, is_bot=False)
        for player_id in player_ids
    ]
    _add_participants(participant_repo, game_id, *participants)


def test_active_games_lists_open_games_with_player_names(
    report_service: ReportService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")
    game = game_repo.create_active_game(
        name="Friday Night",
        map_size=12,
        terrain="Drylands",
        game_type="Domination",
        target_score=10000,
        game_timer="One Week",
    )
    _add_human_participants(participant_repo, game.id, alice.id, bob.id)

    dto = report_service.active_games()

    assert isinstance(dto, ReportDTO)
    assert dto.title
    assert len(dto.fields) == 1
    field = dto.fields[0]
    assert isinstance(field, ReportField)
    assert field.label == "Friday Night"
    assert "Alice" in field.value
    assert "Bob" in field.value
    assert "Drylands" in field.value
    assert "12" in field.value


def test_active_games_excludes_completed_games(
    report_service: ReportService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    active = game_repo.create_active_game(name="Still Going")
    done = game_repo.create_active_game(name="Finished")
    _add_human_participants(participant_repo, active.id, alice.id)
    _add_human_participants(participant_repo, done.id, alice.id)
    game_repo.complete_game(done.id, alice.id)

    dto = report_service.active_games()

    labels = [field.label for field in dto.fields]
    assert labels == ["Still Going"]


def test_active_games_empty_returns_friendly_dto(
    report_service: ReportService,
) -> None:
    dto = report_service.active_games()

    assert isinstance(dto, ReportDTO)
    assert "No active games" in (dto.title + dto.description)
    assert dto.fields == []


def test_recent_completions_includes_games_within_lookback(
    report_service: ReportService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")
    recent = game_repo.create_active_game(name="Recent Win")
    _add_human_participants(participant_repo, recent.id, alice.id, bob.id)
    completed_at = datetime.now(tz=UTC) - timedelta(days=3)
    game_repo.complete_game(recent.id, alice.id, completed_at=completed_at)

    dto = report_service.recent_completions(lookback_days=7)

    assert len(dto.fields) == 1
    field = dto.fields[0]
    assert field.label == "Recent Win"
    assert "Alice" in field.value
    assert "Bob" in field.value
    assert "winner" in field.value.lower() or "Alice" in field.value


def test_recent_completions_excludes_games_outside_lookback(
    report_service: ReportService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    old_game = game_repo.create_active_game(name="Ancient Match")
    _add_human_participants(participant_repo, old_game.id, alice.id)
    completed_at = datetime.now(tz=UTC) - timedelta(days=30)
    game_repo.complete_game(old_game.id, alice.id, completed_at=completed_at)

    dto = report_service.recent_completions(lookback_days=7)

    assert dto.fields == []


def test_recent_completions_empty_returns_friendly_dto(
    report_service: ReportService,
) -> None:
    dto = report_service.recent_completions(lookback_days=14)

    assert isinstance(dto, ReportDTO)
    assert "No recent completions" in (dto.title + dto.description)
    assert dto.fields == []


def test_win_ratios_shows_each_player_record_vs_opponents(
    report_service: ReportService,
    player_repo: PlayerRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")
    carol = player_repo.create(polytopia_name="Carol")
    ratio_repo.upsert_ratio(alice.id, bob.id, wins=5, source="screenshot")
    ratio_repo.upsert_ratio(bob.id, alice.id, wins=3, source="screenshot")
    ratio_repo.upsert_ratio(alice.id, carol.id, wins=2, source="computed")
    ratio_repo.upsert_ratio(carol.id, alice.id, wins=1, source="computed")
    ratio_repo.upsert_ratio(bob.id, carol.id, wins=4, source="computed")
    ratio_repo.upsert_ratio(carol.id, bob.id, wins=4, source="computed")

    dto = report_service.win_ratios()

    by_label = {field.label: field.value for field in dto.fields}
    assert "Alice" in by_label
    assert "Bob" in by_label
    assert "Carol" in by_label
    assert "5" in by_label["Alice"] and "3" in by_label["Alice"]
    assert "Bob" in by_label["Alice"]
    assert "Carol" in by_label["Alice"]
    assert "4" in by_label["Bob"] and "Carol" in by_label["Bob"]


def test_win_ratios_empty_returns_friendly_dto(
    report_service: ReportService,
) -> None:
    dto = report_service.win_ratios()

    assert isinstance(dto, ReportDTO)
    assert "no win ratio" in (dto.title + dto.description).lower()
    assert dto.fields == []


def test_format_report_text_renders_readable_output() -> None:
    dto = ReportDTO(
        title="Active Games",
        description="Games currently in progress.",
        fields=[
            ReportField(label="Friday Night", value="Alice, Bob · Drylands · 12"),
        ],
        footer="Updated just now",
    )

    text = format_report_text(dto)

    assert "Active Games" in text
    assert "Games currently in progress." in text
    assert "Friday Night" in text
    assert "Alice, Bob" in text
    assert "Updated just now" in text


def test_format_report_text_handles_empty_fields() -> None:
    dto = ReportDTO(
        title="Active Games",
        description="No active games.",
        fields=[],
    )

    text = format_report_text(dto)

    assert "Active Games" in text
    assert "No active games." in text


def test_active_games_participants_separate_humans_from_bots(
    report_service: ReportService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")
    crazy_bot = player_repo.create(polytopia_name="Crazy Bot")
    game = game_repo.create_active_game(name="Bots Included")
    _add_participants(
        participant_repo,
        game.id,
        GameParticipantInput(player_id=alice.id, is_bot=False),
        GameParticipantInput(player_id=bob.id, is_bot=False),
        GameParticipantInput(player_id=crazy_bot.id, is_bot=True),
    )

    dto = report_service.active_games()
    field = dto.fields[0]

    assert "Alice" in field.value
    assert "Bob" in field.value
    assert "Crazy Bot" not in field.value
    assert "Bots: 1" in field.value


def test_recent_completions_participants_separate_humans_from_bots(
    report_service: ReportService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    hard_bot = player_repo.create(polytopia_name="Hard Bot")
    game = game_repo.create_active_game(name="Bot Match")
    _add_participants(
        participant_repo,
        game.id,
        GameParticipantInput(player_id=alice.id, is_bot=False),
        GameParticipantInput(player_id=hard_bot.id, is_bot=True),
    )
    game_repo.complete_game(game.id, alice.id)

    dto = report_service.recent_completions(lookback_days=7)
    field = dto.fields[0]

    assert "Alice" in field.value
    assert "Hard Bot" not in field.value
    assert "Bots: 1" in field.value
