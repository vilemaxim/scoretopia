"""Database connection and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SCHEMA_VERSION = 1


def open_database(path: str = ":memory:") -> sqlite3.Connection:
    """Open a SQLite database, enable foreign keys, and apply schema if needed."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    _initialize_schema(conn)
    return conn


def _initialize_schema(conn: sqlite3.Connection) -> None:
    existing = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_version'
        """
    ).fetchone()
    if existing is not None:
        return

    schema_sql = _SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))
    conn.commit()
