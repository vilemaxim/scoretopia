"""Database connection and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SCHEMA_VERSION = 2


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


def _initialize_schema(conn: sqlite3.Connection) -> None:
    """Create missing tables and record the schema version.

    Every statement in ``schema.sql`` is ``IF NOT EXISTS``, so running it on a
    database from an earlier version adds only the tables that version lacked
    (version 2 added ``capture_manifests``).
    """
    existing = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_version'
        """
    ).fetchone()
    if existing is not None:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        if row is not None and row[0] is not None and row[0] >= _SCHEMA_VERSION:
            return

    conn.executescript(_SCHEMA_PATH.read_text())
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))
    conn.commit()
