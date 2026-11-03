"""Database connection and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SCHEMA_VERSION = 3

# capture_manifests as of schema 3, for rebuilding a table from an earlier
# version: SQLite cannot alter a CHECK constraint in place. Keep in step with
# schema.sql.
_CAPTURE_MANIFESTS_V3 = """
CREATE TABLE capture_manifests_v3 (
    directory TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('setup', 'result')),
    game_key TEXT,
    game_id INTEGER REFERENCES games (id),
    outcome TEXT NOT NULL CHECK (outcome IN ('posted', 'duplicate', 'seeded')),
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def open_database(
    path: str = ":memory:",
    *,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite database, enable foreign keys, and apply schema if needed.

    ``check_same_thread=False`` is for a connection that one worker thread at a
    time uses, such as the bot's capture poll.
    """
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys = ON")
    _initialize_schema(conn)
    return conn


def _stored_version(conn: sqlite3.Connection) -> int | None:
    existing = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_version'
        """
    ).fetchone()
    if existing is None:
        return None
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _initialize_schema(conn: sqlite3.Connection) -> None:
    """Create missing tables, migrate older ones, and record the schema version.

    Every statement in ``schema.sql`` is ``IF NOT EXISTS``, so running it on a
    database from an earlier version adds only the tables that version lacked
    (version 2 added ``capture_manifests``). Version 3 widened
    ``capture_manifests.outcome`` to accept ``seeded``; a table created before
    that keeps its old ``CHECK``, so it is rebuilt with its rows.
    """
    version = _stored_version(conn)
    if version is not None and version >= _SCHEMA_VERSION:
        return

    conn.executescript(_SCHEMA_PATH.read_text())
    conn.execute("BEGIN")
    try:
        if not _capture_manifests_accepts_seeded(conn):
            _rebuild_capture_manifests(conn)
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,)
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _capture_manifests_accepts_seeded(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'capture_manifests'
        """
    ).fetchone()
    return row is not None and "'seeded'" in str(row[0])


def _rebuild_capture_manifests(conn: sqlite3.Connection) -> None:
    """Copy ``capture_manifests`` into the version-3 definition, in the caller's
    transaction: new table, copy, drop, rename, recreate the index."""
    conn.execute(_CAPTURE_MANIFESTS_V3)
    conn.execute(
        """
        INSERT INTO capture_manifests_v3
            (directory, kind, game_key, game_id, outcome, processed_at)
        SELECT directory, kind, game_key, game_id, outcome, processed_at
        FROM capture_manifests
        """
    )
    conn.execute("DROP TABLE capture_manifests")
    conn.execute("ALTER TABLE capture_manifests_v3 RENAME TO capture_manifests")
    conn.execute(
        """
        CREATE INDEX capture_manifests_game_key
            ON capture_manifests (kind, game_key)
        """
    )
