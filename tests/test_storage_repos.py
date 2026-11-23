"""Tests for SQLite schema and repository layer (Task 005)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from scoretopia.storage.db import open_database
from scoretopia.storage.errors import DuplicatePolytopiaNameError
from scoretopia.storage.models import GameParticipantInput
from scoretopia.storage.repos import (
    CaptureManifestRepo,
    GameParticipantRepo,
    GameRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)

EXPECTED_TABLES = frozenset(
    {
        "players",
        "games",
        "game_participants",
        "player_pair_ratios",
        "capture_manifests",
        "schema_version",
    }
)


REVIEW_FLOW_TABLES = frozenset({"pending_interactions", "disputes"})


def test_schema_has_no_review_flow_tables(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert not tables & REVIEW_FLOW_TABLES


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


def test_schema_creates_all_tables(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    table_names = {row[0] for row in rows}
    assert EXPECTED_TABLES.issubset(table_names)


def test_schema_version_table_has_initial_version(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row is not None
    assert row[0] >= 1


def test_foreign_keys_are_enabled(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_player_create_and_get_by_id_round_trip(player_repo: PlayerRepo) -> None:
    created = player_repo.create(
        polytopia_name="Jeff",
        discord_user_id="111",
        discord_display_name="jeff-discord",
    )

    fetched = player_repo.get_by_id(created.id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.polytopia_name == "Jeff"
    assert fetched.discord_user_id == "111"
    assert fetched.discord_display_name == "jeff-discord"


def test_player_get_by_polytopia_name_normalizes_lookup(
    player_repo: PlayerRepo,
) -> None:
    player_repo.create(polytopia_name="Jeff")

    assert player_repo.get_by_polytopia_name("  JEFF  ") is not None
    assert player_repo.get_by_polytopia_name("jeff") is not None


def test_player_get_by_discord_id(player_repo: PlayerRepo) -> None:
    created = player_repo.create(
        polytopia_name="Alice",
        discord_user_id="222",
        discord_display_name="alice-discord",
    )

    fetched = player_repo.get_by_discord_id("222")

    assert fetched is not None
    assert fetched.id == created.id


def test_player_update_discord_link(player_repo: PlayerRepo) -> None:
    created = player_repo.create(polytopia_name="Bob")

    updated = player_repo.update_discord_link(
        created.id,
        discord_user_id="333",
        discord_display_name="bob-discord",
    )

    assert updated.discord_user_id == "333"
    assert updated.discord_display_name == "bob-discord"
    assert player_repo.get_by_discord_id("333") is not None


def test_player_clear_discord_link(player_repo: PlayerRepo) -> None:
    created = player_repo.create(
        polytopia_name="Dana",
        discord_user_id="444",
        discord_display_name="dana-discord",
    )

    cleared = player_repo.clear_discord_link(created.id)

    assert cleared.discord_user_id is None
    assert cleared.discord_display_name is None
    assert player_repo.get_by_discord_id("444") is None


def test_player_create_duplicate_polytopia_name_raises(
    player_repo: PlayerRepo,
) -> None:
    player_repo.create(polytopia_name="Carol")

    with pytest.raises(DuplicatePolytopiaNameError):
        player_repo.create(polytopia_name="  CAROL  ")


def test_create_active_game_and_get_by_id(game_repo: GameRepo) -> None:
    game = game_repo.create_active_game(
        name="Friday Night",
        map_size=12,
        terrain="Drylands",
        game_type="Domination",
        target_score=10000,
        game_timer="One Week",
    )

    fetched = game_repo.get_by_id(game.id)

    assert fetched is not None
    assert fetched.id == game.id
    assert fetched.name == "Friday Night"
    assert fetched.status == "active"
    assert fetched.map_size == 12
    assert fetched.terrain == "Drylands"
    assert fetched.game_type == "Domination"
    assert fetched.target_score == 10000
    assert fetched.game_timer == "One Week"
    assert fetched.winner_player_id is None


def test_list_active_excludes_completed(
    game_repo: GameRepo,
    player_repo: PlayerRepo,
) -> None:
    active = game_repo.create_active_game(name="Active Game")
    completed = game_repo.create_active_game(name="Done Game")
    winner = player_repo.create(polytopia_name="Winner")
    game_repo.complete_game(completed.id, winner.id)

    active_ids = {game.id for game in game_repo.list_active()}

    assert active.id in active_ids
    assert completed.id not in active_ids


def test_list_active_by_participants_exact_human_set_match(
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")
    game = game_repo.create_active_game(name="Match Me")
    participant_repo.add_participants(
        game.id,
        [
            GameParticipantInput(player_id=alice.id, tribe="Imperius", is_bot=False),
            GameParticipantInput(player_id=bob.id, tribe="Bardur", is_bot=False),
            GameParticipantInput(
                player_id=player_repo.create(polytopia_name="Crazy Bot").id,
                tribe="Kickoo",
                is_bot=True,
            ),
        ],
    )

    matches = game_repo.list_active_by_participants({"Alice", "Bob"})

    assert [match.id for match in matches] == [game.id]


def test_list_active_by_participants_excludes_bots_from_matching(
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")
    game = game_repo.create_active_game(name="Humans Only")
    participant_repo.add_participants(
        game.id,
        [
            GameParticipantInput(player_id=alice.id, tribe="Imperius", is_bot=False),
            GameParticipantInput(player_id=bob.id, tribe="Bardur", is_bot=False),
        ],
    )

    matches = game_repo.list_active_by_participants({"Alice", "Bob", "Crazy Bot"})

    assert [match.id for match in matches] == [game.id]


def test_list_active_by_participants_no_match_when_set_differs(
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")
    game = game_repo.create_active_game(name="Different Humans")
    participant_repo.add_participants(
        game.id,
        [
            GameParticipantInput(player_id=alice.id, tribe="Imperius", is_bot=False),
            GameParticipantInput(player_id=bob.id, tribe="Bardur", is_bot=False),
        ],
    )

    assert game_repo.list_active_by_participants({"Alice"}) == []
    assert game_repo.list_active_by_participants({"Alice", "Carol"}) == []


def test_complete_game(
    game_repo: GameRepo,
    player_repo: PlayerRepo,
) -> None:
    game = game_repo.create_active_game(name="Finale")
    winner = player_repo.create(polytopia_name="Champion")
    completed_at = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)

    completed = game_repo.complete_game(
        game.id,
        winner.id,
        completed_at=completed_at,
    )

    assert completed.status == "completed"
    assert completed.winner_player_id == winner.id
    assert completed.completed_at == completed_at
    assert game_repo.get_by_id(game.id) is not None
    assert game_repo.get_by_id(game.id).status == "completed"


def test_add_participants_round_trip(
    game_repo: GameRepo,
    player_repo: PlayerRepo,
    participant_repo: GameParticipantRepo,
    conn: sqlite3.Connection,
) -> None:
    game = game_repo.create_active_game(name="Participants")
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")

    participant_repo.add_participants(
        game.id,
        [
            GameParticipantInput(
                player_id=alice.id,
                tribe="Imperius",
                score=1200,
                placement=1,
                is_bot=False,
            ),
            GameParticipantInput(
                player_id=bob.id,
                tribe="Bardur",
                score=900,
                placement=2,
                is_bot=False,
            ),
        ],
    )

    rows = conn.execute(
        """
        SELECT player_id, tribe, score, placement, is_bot
        FROM game_participants
        WHERE game_id = ?
        ORDER BY placement
        """,
        (game.id,),
    ).fetchall()

    assert rows == [
        (alice.id, "Imperius", 1200, 1, 0),
        (bob.id, "Bardur", 900, 2, 0),
    ]


def test_player_pair_ratio_get_ratio_returns_none_when_missing(
    ratio_repo: PlayerPairRatioRepo,
    player_repo: PlayerRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")

    assert ratio_repo.get_ratio(alice.id, bob.id) is None


def test_player_pair_ratio_upsert_round_trip(
    ratio_repo: PlayerPairRatioRepo,
    player_repo: PlayerRepo,
) -> None:
    alice = player_repo.create(polytopia_name="Alice")
    bob = player_repo.create(polytopia_name="Bob")

    created = ratio_repo.upsert_ratio(
        alice.id,
        bob.id,
        wins=5,
        source="computed",
    )
    updated = ratio_repo.upsert_ratio(
        alice.id,
        bob.id,
        wins=8,
        source="screenshot",
    )
    fetched = ratio_repo.get_ratio(alice.id, bob.id)

    assert created.wins == 5
    assert created.source == "computed"
    assert updated.wins == 8
    assert updated.source == "screenshot"
    assert fetched is not None
    assert fetched.wins == 8
    assert fetched.source == "screenshot"


def test_version_1_database_gains_capture_manifests_on_open(tmp_path) -> None:
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
        INSERT INTO games (name) VALUES ('kept');
        """
    )
    old.commit()
    old.close()

    upgraded = open_database(path)

    tables = {
        row[0]
        for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "capture_manifests" in tables
    assert not tables & REVIEW_FLOW_TABLES
    assert upgraded.execute("SELECT name FROM games").fetchall() == [("kept",)]
    assert upgraded.execute("SELECT version FROM schema_version").fetchall() == [(4,)]
    upgraded.close()


_V2_CAPTURE_MANIFESTS = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version (version) VALUES (2);
CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
INSERT INTO games (name) VALUES ('kept');
CREATE TABLE capture_manifests (
    directory TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('setup', 'result')),
    game_key TEXT,
    game_id INTEGER REFERENCES games (id),
    outcome TEXT NOT NULL CHECK (outcome IN ('posted', 'duplicate')),
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX capture_manifests_game_key ON capture_manifests (kind, game_key);
INSERT INTO capture_manifests
    (directory, kind, game_key, game_id, outcome, processed_at)
VALUES ('setup/a', 'setup', 'g-1', 1, 'posted', '2026-10-20 10:00:00'),
       ('setup/b', 'setup', 'g-1', NULL, 'duplicate', '2026-10-21 10:00:00');
"""


def _version_2_database(tmp_path) -> str:
    path = str(tmp_path / "v2.db")
    old = sqlite3.connect(path)
    old.executescript(_V2_CAPTURE_MANIFESTS)
    old.commit()
    old.close()
    return path


def test_version_2_database_migrates_capture_manifests_keeping_rows(tmp_path) -> None:
    upgraded = open_database(_version_2_database(tmp_path))

    assert upgraded.execute("SELECT version FROM schema_version").fetchall() == [(4,)]
    assert upgraded.execute(
        """
        SELECT directory, kind, game_key, game_id, outcome, processed_at
        FROM capture_manifests ORDER BY directory
        """
    ).fetchall() == [
        ("setup/a", "setup", "g-1", 1, "posted", "2026-10-20 10:00:00"),
        ("setup/b", "setup", "g-1", None, "duplicate", "2026-10-21 10:00:00"),
    ]
    repo = CaptureManifestRepo(upgraded)
    assert repo.game_id_for_key("setup", "g-1") == 1
    repo.record(
        directory="result/c",
        kind="result",
        game_key=None,
        game_id=None,
        outcome="seeded",
    )
    assert repo.is_processed("result/c")
    indexes = {
        row[0]
        for row in upgraded.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type='index' AND tbl_name='capture_manifests'"
        )
    }
    assert "capture_manifests_game_key" in indexes
    tables = {
        row[0]
        for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "capture_manifests_v3" not in tables
    upgraded.close()


def test_migrated_database_is_left_alone_on_the_next_open(tmp_path) -> None:
    path = _version_2_database(tmp_path)
    open_database(path).close()

    reopened = open_database(path)

    assert reopened.execute("SELECT COUNT(*) FROM capture_manifests").fetchone() == (2,)
    assert reopened.execute("SELECT version FROM schema_version").fetchall() == [(4,)]
    reopened.close()


def test_outcome_still_rejects_an_unknown_value(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        CaptureManifestRepo(conn).record(
            directory="setup/x",
            kind="setup",
            game_key=None,
            game_id=None,
            outcome="other",
        )


def test_capture_manifest_record_and_lookup(
    conn: sqlite3.Connection, game_repo: GameRepo
) -> None:
    repo = CaptureManifestRepo(conn)
    game = game_repo.create_active_game(name="Captured")

    assert repo.is_processed("setup/k1") is False
    repo.record(
        directory="setup/k1",
        kind="setup",
        game_key="g-1",
        game_id=game.id,
        outcome="posted",
    )

    assert repo.is_processed("setup/k1") is True
    assert repo.game_id_for_key("setup", "g-1") == game.id
    assert repo.game_id_for_key("result", "g-1") is None
    assert repo.game_id_for_key("setup", "g-2") is None


def test_capture_manifest_duplicate_rows_do_not_shadow_the_posted_one(
    conn: sqlite3.Connection, game_repo: GameRepo
) -> None:
    repo = CaptureManifestRepo(conn)
    game = game_repo.create_active_game(name="Captured")
    repo.record(
        directory="setup/k1",
        kind="setup",
        game_key="g-1",
        game_id=game.id,
        outcome="posted",
    )
    repo.record(
        directory="setup/k2",
        kind="setup",
        game_key="g-1",
        game_id=None,
        outcome="duplicate",
    )

    assert repo.game_id_for_key("setup", "g-1") == game.id
    assert repo.is_processed("setup/k2") is True


_V3_WITH_REVIEW_TABLES = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version (version) VALUES (3);
CREATE TABLE players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    polytopia_name TEXT NOT NULL,
    polytopia_name_normalized TEXT NOT NULL UNIQUE,
    discord_user_id TEXT UNIQUE,
    discord_display_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO players (polytopia_name, polytopia_name_normalized)
VALUES ('kept', 'kept');
CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
INSERT INTO games (name) VALUES ('kept');
CREATE TABLE capture_manifests (
    directory TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('setup', 'result')),
    game_key TEXT,
    game_id INTEGER REFERENCES games (id),
    outcome TEXT NOT NULL CHECK (outcome IN ('posted', 'duplicate', 'seeded')),
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO capture_manifests (directory, kind, game_key, game_id, outcome)
VALUES ('setup/a', 'setup', 'g-1', 1, 'posted');
CREATE TABLE pending_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    discord_user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'disputed')),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO pending_interactions (kind, discord_user_id, status, payload)
VALUES ('confirm_game_end', 'u', 'open', '{}');
CREATE TABLE disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_a_id INTEGER NOT NULL REFERENCES players (id),
    player_b_id INTEGER NOT NULL REFERENCES players (id),
    submitter_player_id INTEGER NOT NULL REFERENCES players (id),
    rejector_player_id INTEGER NOT NULL REFERENCES players (id),
    claimed_wins_a INTEGER NOT NULL,
    claimed_wins_b INTEGER NOT NULL,
    screenshot_path TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO disputes
    (player_a_id, player_b_id, submitter_player_id, rejector_player_id,
     claimed_wins_a, claimed_wins_b, status)
VALUES (1, 1, 1, 1, 1, 0, 'open');
"""


def test_version_3_database_drops_review_flow_tables_keeping_the_rest(
    tmp_path,
) -> None:
    path = str(tmp_path / "v3.db")
    old = sqlite3.connect(path)
    old.executescript(_V3_WITH_REVIEW_TABLES)
    old.commit()
    old.close()

    upgraded = open_database(path)

    tables = {
        row[0]
        for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert not tables & REVIEW_FLOW_TABLES
    assert upgraded.execute("SELECT version FROM schema_version").fetchall() == [(4,)]
    assert upgraded.execute("SELECT name FROM games").fetchall() == [("kept",)]
    assert upgraded.execute("SELECT directory FROM capture_manifests").fetchall() == [
        ("setup/a",)
    ]
    upgraded.close()

    reopened = open_database(path)
    assert reopened.execute("SELECT version FROM schema_version").fetchall() == [(4,)]
    reopened.close()
