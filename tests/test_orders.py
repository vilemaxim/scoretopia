"""The orders file: recording writes it, seat control reads it.

Invented names only, no network, and nothing written outside ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from scoretopia.domain.players import PlayerService
from scoretopia.orders import (
    Orders,
    OrdersKeeper,
    UnknownOrdersVersionError,
    read_orders,
    write_orders,
)
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import PlayerRepo


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


def test_orders_written_are_the_orders_read(tmp_path: Path) -> None:
    path = tmp_path / "orders.json"

    write_orders(path, Orders(known_players=("Alpha", "Bravo")))

    assert read_orders(path) == Orders(known_players=("Alpha", "Bravo"))


def test_the_file_states_its_version_and_an_empty_list_of_games_to_start(
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.json"

    write_orders(path, Orders(known_players=("Alpha",)))

    assert json.loads(path.read_text()) == {
        "version": 1,
        "known_players": ["Alpha"],
        "start_games": [],
    }


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "{not json",
        "[]",
        "{}",
        '{"known_players": ["Alpha"]}',
        '{"version": 1}',
        '{"version": 1, "known_players": "Alpha"}',
    ],
)
def test_a_missing_empty_or_malformed_file_reads_as_no_known_players(
    tmp_path: Path,
    content: str | None,
) -> None:
    path = tmp_path / "orders.json"
    if content is not None:
        path.write_text(content)

    assert read_orders(path) == Orders()


def test_an_unknown_version_is_refused_with_a_clear_message(tmp_path: Path) -> None:
    path = tmp_path / "orders.json"
    path.write_text(json.dumps({"version": 2, "known_players": ["Alpha"]}))

    with pytest.raises(UnknownOrdersVersionError, match="version 2"):
        read_orders(path)


def test_the_old_file_stays_whole_until_the_new_one_replaces_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "orders.json"
    write_orders(path, Orders(known_players=("Alpha",)))
    seen_before_replace: list[Orders] = []
    real_replace = os.replace

    def watching_replace(source: object, target: object) -> None:
        seen_before_replace.append(read_orders(path))
        real_replace(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", watching_replace)

    write_orders(path, Orders(known_players=("Alpha", "Bravo")))

    assert seen_before_replace == [Orders(known_players=("Alpha",))]
    assert read_orders(path) == Orders(known_players=("Alpha", "Bravo"))
    assert [entry.name for entry in tmp_path.iterdir()] == ["orders.json"]


def test_a_write_that_fails_leaves_the_old_file_and_nothing_else(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "orders.json"
    write_orders(path, Orders(known_players=("Alpha",)))

    def failing_replace(source: object, target: object) -> None:
        raise OSError("disk says no")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        write_orders(path, Orders(known_players=("Bravo",)))

    assert read_orders(path) == Orders(known_players=("Alpha",))
    assert [entry.name for entry in tmp_path.iterdir()] == ["orders.json"]


def test_writing_creates_the_directory_it_needs(tmp_path: Path) -> None:
    path = tmp_path / "shared" / "orders.json"

    write_orders(path, Orders(known_players=("Alpha",)))

    assert read_orders(path) == Orders(known_players=("Alpha",))


# --- recording keeps the file current ---------------------------------------


def _register(service: PlayerService, discord_id: str, name: str) -> None:
    service.register(
        discord_user_id=discord_id,
        discord_display_name="someone",
        polytopia_name=name,
    )


def test_refresh_writes_the_registered_players_in_name_order(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.json"
    repo = PlayerRepo(conn)
    service = PlayerService(repo)
    _register(service, "1002", "Bravo")
    _register(service, "1001", "Alpha")

    OrdersKeeper(repo, path).refresh()

    assert read_orders(path) == Orders(known_players=("Alpha", "Bravo"))


def test_a_player_seen_in_a_game_but_not_registered_is_not_a_known_player(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.json"
    repo = PlayerRepo(conn)
    service = PlayerService(repo)
    _register(service, "1001", "Alpha")
    service.resolve_or_create_polytopia_name("Stranger")

    OrdersKeeper(repo, path).refresh()

    assert read_orders(path) == Orders(known_players=("Alpha",))


def test_registering_a_player_rewrites_the_file_with_their_name(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.json"
    repo = PlayerRepo(conn)
    keeper = OrdersKeeper(repo, path)
    service = PlayerService(repo, on_players_changed=keeper.refresh)
    keeper.refresh()
    assert read_orders(path) == Orders()

    _register(service, "1001", "Alpha")

    assert read_orders(path) == Orders(known_players=("Alpha",))


def test_linking_a_player_first_seen_in_a_game_rewrites_the_file(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.json"
    repo = PlayerRepo(conn)
    keeper = OrdersKeeper(repo, path)
    service = PlayerService(repo, on_players_changed=keeper.refresh)
    service.resolve_or_create_polytopia_name("Alpha")
    assert read_orders(path) == Orders()

    _register(service, "1001", "Alpha")

    assert read_orders(path) == Orders(known_players=("Alpha",))


def test_a_refused_registration_does_not_change_the_file(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.json"
    repo = PlayerRepo(conn)
    keeper = OrdersKeeper(repo, path)
    service = PlayerService(repo, on_players_changed=keeper.refresh)
    _register(service, "1001", "Alpha")
    before = path.stat().st_mtime_ns

    _register(service, "1002", "Alpha")

    assert read_orders(path) == Orders(known_players=("Alpha",))
    assert path.stat().st_mtime_ns == before


def test_a_failed_rewrite_after_a_change_does_not_undo_the_registration(
    conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "orders.json"
    repo = PlayerRepo(conn)
    keeper = OrdersKeeper(repo, path)
    service = PlayerService(repo, on_players_changed=keeper.refresh_or_log)

    def failing_replace(source: object, target: object) -> None:
        raise OSError("disk says no")

    monkeypatch.setattr(os, "replace", failing_replace)

    _register(service, "1001", "Alpha")

    assert repo.get_by_discord_id("1001") is not None
    assert "orders file not written" in caplog.text


def test_recording_writes_the_orders_as_it_starts_and_after_each_registration(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    from scoretopia import cli
    from scoretopia.config import load_config

    example = Path(__file__).resolve().parent.parent / "config"
    config_file = tmp_path / "scoretopia.yaml"
    config_file.write_text((example / "scoretopia.example.yaml").read_text())
    config = load_config(config_file)

    service = cli._build_player_service(config, PlayerRepo(conn))

    assert config.orders.path == tmp_path / "data" / "orders.json"
    assert config.orders.path.is_file()
    assert read_orders(config.orders.path) == Orders()

    _register(service, "1001", "Alpha")

    assert read_orders(config.orders.path) == Orders(known_players=("Alpha",))


def test_the_orders_file_is_readable_by_its_group_and_not_by_others(
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.json"

    write_orders(path, Orders(known_players=("Alpha",)))

    assert path.stat().st_mode & 0o777 == 0o640
