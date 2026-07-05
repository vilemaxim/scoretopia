"""Tests for game lifecycle domain service (Task 008)."""

from __future__ import annotations

import sqlite3

import pytest

from scoretopia.domain.games import GameService
from scoretopia.domain.results import (
    CompleteResult,
    MatchOutcome,
    MatchResult,
    RejectResult,
)
from scoretopia.screenshot.models import (
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndPlayer,
)
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import (
    GameParticipantRepo,
    GameRepo,
    PendingInteractionRepo,
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
def pending_repo(conn: sqlite3.Connection) -> PendingInteractionRepo:
    return PendingInteractionRepo(conn)


@pytest.fixture
def ratio_repo(conn: sqlite3.Connection) -> PlayerPairRatioRepo:
    return PlayerPairRatioRepo(conn)


@pytest.fixture
def game_service(
    game_repo: GameRepo,
    participant_repo: GameParticipantRepo,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> GameService:
    return GameService(
        game_repo,
        participant_repo,
        player_repo,
        pending_repo,
        ratio_repo,
    )


def _game_end_extraction(
    *players: tuple[str, int, bool],
    winner: str | None = None,
) -> GameEndExtraction:
    """Build a game-end extraction from (name, score, is_winner) tuples."""
    winner = winner or players[0][0]
    return GameEndExtraction(
        winner=winner,
        players=tuple(
            GameEndPlayer(name=name, score=score, is_winner=is_winner)
            for name, score, is_winner in players
        ),
    )


def test_start_game_persists_settings_and_participants(
    game_service: GameService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    conn: sqlite3.Connection,
) -> None:
    extraction = GameBasicsExtraction(
        game_name="Friday Night",
        map_size=12,
        terrain="Drylands",
        game_type="Domination",
        target_score=10000,
        game_timer="One Week",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
            GameBasicsPlayer(name="Crazy Bot"),
        ),
    )

    game = game_service.start_game(extraction=extraction, uploader_id="discord-111")

    assert game.status == "active"
    assert game.name == "Friday Night"
    assert game.map_size == 12
    assert game.terrain == "Drylands"
    assert game.game_type == "Domination"
    assert game.target_score == 10000
    assert game.game_timer == "One Week"

    fetched = game_repo.get_by_id(game.id)
    assert fetched is not None
    assert fetched.name == "Friday Night"

    rows = conn.execute(
        """
        SELECT p.polytopia_name, gp.is_bot
        FROM game_participants gp
        JOIN players p ON p.id = gp.player_id
        WHERE gp.game_id = ?
        ORDER BY p.polytopia_name
        """,
        (game.id,),
    ).fetchall()
    assert rows == [
        ("Alice", 0),
        ("Bob", 0),
        ("Crazy Bot", 1),
    ]

    alice = player_repo.get_by_polytopia_name("Alice")
    assert alice is not None
    assert alice.discord_user_id == "discord-111"


def test_match_game_end_finds_active_game_with_ocr_name_tolerance(
    game_service: GameService,
) -> None:
    start = GameBasicsExtraction(
        game_name="OCR Match",
        players=(
            GameBasicsPlayer(name="Diremouse01"),
            GameBasicsPlayer(name="Lord Union 409"),
            GameBasicsPlayer(name="vilemaxim"),
        ),
    )
    game_service.start_game(extraction=start, uploader_id="uploader-1")

    end = _game_end_extraction(
        ("DiremouseO1", 12000, True),
        ("Lord Union 409", 8000, False),
        ("vilemaxim", 6000, False),
    )
    result = game_service.match_game_end(end)

    assert isinstance(result, MatchResult)
    assert result.outcome == MatchOutcome.ONE
    assert len(result.games) == 1
    assert result.games[0].name == "OCR Match"


def test_match_game_end_returns_none_when_no_active_game(
    game_service: GameService,
) -> None:
    end = _game_end_extraction(
        ("Alice", 1000, True),
        ("Bob", 500, False),
    )
    result = game_service.match_game_end(end)

    assert result.outcome == MatchOutcome.NONE
    assert result.games == ()


def test_match_game_end_returns_many_when_multiple_active_games_match(
    game_service: GameService,
) -> None:
    roster = (
        GameBasicsPlayer(name="Alice"),
        GameBasicsPlayer(name="Bob"),
    )
    game_service.start_game(
        extraction=GameBasicsExtraction(game_name="Game A", players=roster),
        uploader_id="uploader-a",
    )
    game_service.start_game(
        extraction=GameBasicsExtraction(game_name="Game B", players=roster),
        uploader_id="uploader-b",
    )

    end = _game_end_extraction(
        ("Alice", 1000, True),
        ("Bob", 500, False),
    )
    result = game_service.match_game_end(end)

    assert result.outcome == MatchOutcome.MANY
    assert len(result.games) == 2
    assert {game.name for game in result.games} == {"Game A", "Game B"}


def test_confirm_game_end_completes_game_and_updates_pair_ratios(
    game_service: GameService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
    conn: sqlite3.Connection,
) -> None:
    start = GameBasicsExtraction(
        game_name="Finale",
        players=(
            GameBasicsPlayer(name="Alice"),
            GameBasicsPlayer(name="Bob"),
            GameBasicsPlayer(name="Carol"),
        ),
    )
    game = game_service.start_game(extraction=start, uploader_id="uploader-2")
    alice = player_repo.get_by_polytopia_name("Alice")
    bob = player_repo.get_by_polytopia_name("Bob")
    carol = player_repo.get_by_polytopia_name("Carol")
    assert alice is not None and bob is not None and carol is not None

    ratio_repo.upsert_ratio(alice.id, bob.id, wins=4, source="computed")

    end = _game_end_extraction(
        ("Alice", 12000, True),
        ("Bob", 9000, False),
        ("Carol", 7000, False),
    )
    pending = pending_repo.create(
        kind="confirm_game_end",
        discord_user_id="uploader-2",
        payload={
            "game_id": game.id,
            "winner": "Alice",
            "extraction": {
                "winner": end.winner,
                "players": [
                    {
                        "name": player.name,
                        "score": player.score,
                        "is_winner": player.is_winner,
                    }
                    for player in end.players
                ],
            },
        },
    )

    result = game_service.confirm_game_end(
        interaction_id=pending.id,
        game_id=game.id,
        confirmer_id="uploader-2",
    )

    assert isinstance(result, CompleteResult)
    completed = game_repo.get_by_id(game.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.winner_player_id == alice.id

    score_rows = conn.execute(
        """
        SELECT p.polytopia_name, gp.score, gp.placement
        FROM game_participants gp
        JOIN players p ON p.id = gp.player_id
        WHERE gp.game_id = ?
        ORDER BY gp.placement
        """,
        (game.id,),
    ).fetchall()
    assert score_rows == [
        ("Alice", 12000, 1),
        ("Bob", 9000, 2),
        ("Carol", 7000, 3),
    ]

    alice_bob = ratio_repo.get_ratio(alice.id, bob.id)
    alice_carol = ratio_repo.get_ratio(alice.id, carol.id)
    assert alice_bob is not None
    assert alice_bob.wins == 5
    assert alice_bob.source == "computed"
    assert alice_carol is not None
    assert alice_carol.wins == 1
    assert alice_carol.source == "computed"

    resolved = pending_repo.get_by_id(pending.id)
    assert resolved is not None
    assert resolved.status == "resolved"


def test_reject_game_end_resolves_interaction_without_completing_game(
    game_service: GameService,
    game_repo: GameRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    start = GameBasicsExtraction(
        game_name="Still Active",
        players=(
            GameBasicsPlayer(name="Alice"),
            GameBasicsPlayer(name="Bob"),
        ),
    )
    game = game_service.start_game(extraction=start, uploader_id="uploader-3")
    pending = pending_repo.create(
        kind="confirm_game_end",
        discord_user_id="uploader-3",
        payload={"game_id": game.id, "winner": "Alice"},
    )

    result = game_service.reject_game_end(
        interaction_id=pending.id,
        confirmer_id="uploader-3",
        note="Wrong game",
    )

    assert isinstance(result, RejectResult)
    assert result.interaction_id == pending.id

    still_active = game_repo.get_by_id(game.id)
    assert still_active is not None
    assert still_active.status == "active"
    assert still_active.winner_player_id is None

    resolved = pending_repo.get_by_id(pending.id)
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.payload.get("rejection_note") == "Wrong game"


def test_reconcile_pending_for_game_auto_completes_matching_game_end(
    game_service: GameService,
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> None:
    pending_repo.create(
        kind="game_end_pending_start",
        discord_user_id="uploader-4",
        payload={
            "winner": "Alice",
            "participant_names": ["Alice", "Bob"],
            "extraction": {
                "winner": "Alice",
                "players": [
                    {"name": "Alice", "score": 5000, "is_winner": True},
                    {"name": "Bob", "score": 3000, "is_winner": False},
                ],
            },
        },
    )

    start = GameBasicsExtraction(
        game_name="Late Start",
        players=(
            GameBasicsPlayer(name="Alice"),
            GameBasicsPlayer(name="Bob"),
        ),
    )
    game = game_service.start_game(extraction=start, uploader_id="uploader-4")

    assert game.status == "completed"

    alice = player_repo.get_by_polytopia_name("Alice")
    bob = player_repo.get_by_polytopia_name("Bob")
    assert alice is not None and bob is not None
    ratio = ratio_repo.get_ratio(alice.id, bob.id)
    assert ratio is not None
    assert ratio.wins == 1
    assert ratio.source == "computed"

    open_pending = pending_repo.list_open_by_kind("game_end_pending_start")
    assert open_pending == []
