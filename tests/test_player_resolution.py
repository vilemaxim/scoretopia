"""Tests for DB-assisted OCR roster resolution (Task 028)."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from scoretopia.screenshot.models import (
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndPlayer,
)
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import PlayerRepo


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def player_repo(conn: sqlite3.Connection) -> PlayerRepo:
    return PlayerRepo(conn)


def _require_resolution_api() -> tuple[Any, Any]:
    try:
        from scoretopia.domain.player_resolution import (
            RosterSlotResolution,
            resolve_roster_slots,
        )
    except ImportError as exc:
        pytest.fail(f"player_resolution module not implemented: {exc}")
    return RosterSlotResolution, resolve_roster_slots


def _as_records(resolved: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in resolved:
        if hasattr(entry, "raw_ocr"):
            records.append(
                {
                    "raw_ocr": entry.raw_ocr,
                    "suggested_name": entry.suggested_name,
                    "confidence": entry.confidence,
                    "match_type": entry.match_type,
                }
            )
            continue
        records.append(
            {
                "raw_ocr": entry["raw_ocr"],
                "suggested_name": entry["suggested_name"],
                "confidence": entry["confidence"],
                "match_type": entry["match_type"],
            }
        )
    return records


def test_resolve_roster_slots_exact_match(player_repo: PlayerRepo) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Alice")

    resolved = resolve_roster_slots(
        ["Alice"],
        player_repo,
        screenshot_type="game_basics",
    )
    records = _as_records(resolved)

    assert len(records) == 1
    assert records[0]["raw_ocr"] == "Alice"
    assert records[0]["suggested_name"] == "Alice"
    assert records[0]["match_type"] == "exact"
    assert records[0]["confidence"] == 1.0


def test_resolve_roster_slots_exact_match_is_case_insensitive(
    player_repo: PlayerRepo,
) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Alice")

    resolved = resolve_roster_slots(
        ["alice"],
        player_repo,
        screenshot_type="game_basics",
    )
    records = _as_records(resolved)

    assert records[0]["match_type"] == "exact"
    assert records[0]["suggested_name"] == "Alice"
    assert records[0]["confidence"] == 1.0


def test_resolve_roster_slots_fuzzy_typo_within_threshold(
    player_repo: PlayerRepo,
) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Robert")

    resolved = resolve_roster_slots(
        ["Roberrt"],
        player_repo,
        screenshot_type="game_basics",
    )
    records = _as_records(resolved)

    assert len(records) == 1
    assert records[0]["raw_ocr"] == "Roberrt"
    assert records[0]["suggested_name"] == "Robert"
    assert records[0]["match_type"] == "fuzzy"
    assert records[0]["confidence"] >= 0.80
    assert records[0]["confidence"] < 1.0


def test_resolve_roster_slots_fuzzy_after_ocr_normalization(
    player_repo: PlayerRepo,
) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Diremouse01")

    resolved = resolve_roster_slots(
        ["DiremousO1"],
        player_repo,
        screenshot_type="game_end",
    )
    records = _as_records(resolved)

    assert records[0]["match_type"] in {"exact", "fuzzy"}
    assert records[0]["suggested_name"] == "Diremouse01"
    assert records[0]["confidence"] >= 0.80


def test_resolve_roster_slots_unknown_name_is_new(player_repo: PlayerRepo) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Alice")

    resolved = resolve_roster_slots(
        ["ZedUnknown"],
        player_repo,
        screenshot_type="game_basics",
    )
    records = _as_records(resolved)

    assert len(records) == 1
    assert records[0]["raw_ocr"] == "ZedUnknown"
    assert records[0]["match_type"] == "new"
    assert records[0]["suggested_name"] in {None, "ZedUnknown"}
    assert records[0]["confidence"] < 0.80


def test_resolve_roster_slots_skips_bot_names(player_repo: PlayerRepo) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Alice")

    resolved = resolve_roster_slots(
        ["Alice", "Crazy Bot", "Hard Bot"],
        player_repo,
        screenshot_type="game_basics",
    )
    records = _as_records(resolved)

    assert [record["raw_ocr"] for record in records] == ["Alice"]
    assert all(
        not str(record["raw_ocr"]).lower().endswith(" bot") for record in records
    )


def test_resolve_roster_slots_game_basics_extraction_humans(
    player_repo: PlayerRepo,
) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Alice")
    player_repo.create(polytopia_name="Robert")

    extraction = GameBasicsExtraction(
        game_name="Factory Basics",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Roberrt"),
            GameBasicsPlayer(name="ZedUnknown"),
            GameBasicsPlayer(name="Crazy Bot"),
        ),
    )
    raw_names = [player.name for player in extraction.players]

    resolved = resolve_roster_slots(
        raw_names,
        player_repo,
        screenshot_type=extraction.screenshot_type,
    )
    records = _as_records(resolved)
    by_raw = {record["raw_ocr"]: record for record in records}

    assert set(by_raw) == {"Alice", "Roberrt", "ZedUnknown"}
    assert by_raw["Alice"]["match_type"] == "exact"
    assert by_raw["Roberrt"]["match_type"] == "fuzzy"
    assert by_raw["Roberrt"]["suggested_name"] == "Robert"
    assert by_raw["ZedUnknown"]["match_type"] == "new"


def test_resolve_roster_slots_game_end_extraction_humans(
    player_repo: PlayerRepo,
) -> None:
    _cls, resolve_roster_slots = _require_resolution_api()
    del _cls
    player_repo.create(polytopia_name="Alice")
    player_repo.create(polytopia_name="Samuel")

    extraction = GameEndExtraction(
        winner="Alice",
        players=(
            GameEndPlayer(name="Alice", is_winner=True),
            GameEndPlayer(name="Samual"),
            GameEndPlayer(name="NewbieX"),
            GameEndPlayer(name="Idle Bot"),
        ),
    )
    raw_names = [player.name for player in extraction.players]

    resolved = resolve_roster_slots(
        raw_names,
        player_repo,
        screenshot_type=extraction.screenshot_type,
    )
    records = _as_records(resolved)
    by_raw = {record["raw_ocr"]: record for record in records}

    assert set(by_raw) == {"Alice", "Samual", "NewbieX"}
    assert by_raw["Alice"]["match_type"] == "exact"
    assert by_raw["Samual"]["match_type"] == "fuzzy"
    assert by_raw["Samual"]["suggested_name"] == "Samuel"
    assert by_raw["NewbieX"]["match_type"] == "new"
