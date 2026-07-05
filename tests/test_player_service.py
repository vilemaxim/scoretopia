"""Tests for player registration and auto-link domain service (Task 006)."""

from __future__ import annotations

import sqlite3

import pytest

from scoretopia.domain.players import PlayerService
from scoretopia.domain.results import RegisterOutcome
from scoretopia.screenshot.models import GameBasicsExtraction, GameBasicsPlayer
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


@pytest.fixture
def player_service(player_repo: PlayerRepo) -> PlayerService:
    return PlayerService(player_repo)


def test_register_creates_player_with_both_names_stored_separately(
    player_service: PlayerService,
    player_repo: PlayerRepo,
) -> None:
    result = player_service.register(
        discord_user_id="111",
        discord_display_name="jeff-discord",
        polytopia_name="Jeff",
    )

    assert result.outcome == RegisterOutcome.SUCCESS
    assert result.player is not None
    assert result.player.polytopia_name == "Jeff"
    assert result.player.discord_user_id == "111"
    assert result.player.discord_display_name == "jeff-discord"

    fetched = player_repo.get_by_discord_id("111")
    assert fetched is not None
    assert fetched.polytopia_name == "Jeff"
    assert fetched.discord_display_name == "jeff-discord"


def test_register_same_discord_user_updates_display_name_keeps_polytopia_link(
    player_service: PlayerService,
    player_repo: PlayerRepo,
) -> None:
    first = player_service.register(
        discord_user_id="222",
        discord_display_name="alice-old",
        polytopia_name="Alice",
    )
    assert first.outcome == RegisterOutcome.SUCCESS
    assert first.player is not None
    original_id = first.player.id

    second = player_service.register(
        discord_user_id="222",
        discord_display_name="alice-new",
        polytopia_name="Alice",
    )

    assert second.outcome == RegisterOutcome.SUCCESS
    assert second.player is not None
    assert second.player.id == original_id
    assert second.player.polytopia_name == "Alice"
    assert second.player.discord_display_name == "alice-new"

    fetched = player_repo.get_by_discord_id("222")
    assert fetched is not None
    assert fetched.id == original_id
    assert fetched.polytopia_name == "Alice"
    assert fetched.discord_display_name == "alice-new"


def test_register_fails_when_polytopia_name_owned_by_another_discord_user(
    player_service: PlayerService,
) -> None:
    owner = player_service.register(
        discord_user_id="333",
        discord_display_name="bob-owner",
        polytopia_name="Bob",
    )
    assert owner.outcome == RegisterOutcome.SUCCESS

    conflict = player_service.register(
        discord_user_id="444",
        discord_display_name="bob-impostor",
        polytopia_name="Bob",
    )

    assert conflict.outcome == RegisterOutcome.ALREADY_LINKED_TO_OTHER
    assert conflict.player is None


def test_register_normalizes_polytopia_name_for_conflict_check(
    player_service: PlayerService,
) -> None:
    player_service.register(
        discord_user_id="555",
        discord_display_name="carol",
        polytopia_name="Carol",
    )

    conflict = player_service.register(
        discord_user_id="666",
        discord_display_name="carol-2",
        polytopia_name="  CAROL  ",
    )

    assert conflict.outcome == RegisterOutcome.ALREADY_LINKED_TO_OTHER


def test_auto_link_binds_is_you_player_to_uploader_on_first_sighting(
    player_service: PlayerService,
    player_repo: PlayerRepo,
) -> None:
    existing = player_service.resolve_or_create_polytopia_name("Diremouse01")
    assert existing.discord_user_id is None

    extraction = GameBasicsExtraction(
        players=(
            GameBasicsPlayer(name="Alice", is_you=False),
            GameBasicsPlayer(name="Diremouse01", is_you=True),
        ),
    )

    linked = player_service.auto_link_from_game_basics(
        uploader_discord_id="777",
        extraction=extraction,
    )

    assert linked is not None
    assert linked.id == existing.id
    assert linked.polytopia_name == "Diremouse01"
    assert linked.discord_user_id == "777"

    fetched = player_repo.get_by_polytopia_name("diremouse01")
    assert fetched is not None
    assert fetched.discord_user_id == "777"


def test_auto_link_no_ops_when_polytopia_name_already_linked_to_same_user(
    player_service: PlayerService,
    player_repo: PlayerRepo,
) -> None:
    registered = player_service.register(
        discord_user_id="888",
        discord_display_name="eve-discord",
        polytopia_name="Eve",
    )
    assert registered.outcome == RegisterOutcome.SUCCESS
    assert registered.player is not None

    extraction = GameBasicsExtraction(
        players=(
            GameBasicsPlayer(name="Frank", is_you=False),
            GameBasicsPlayer(name="Eve", is_you=True),
        ),
    )

    linked = player_service.auto_link_from_game_basics(
        uploader_discord_id="888",
        extraction=extraction,
    )

    assert linked is not None
    assert linked.id == registered.player.id
    assert linked.discord_user_id == "888"
    assert linked.discord_display_name == "eve-discord"

    fetched = player_repo.get_by_discord_id("888")
    assert fetched is not None
    assert fetched.id == registered.player.id


def test_auto_link_returns_none_when_no_is_you_player(
    player_service: PlayerService,
) -> None:
    extraction = GameBasicsExtraction(
        players=(
            GameBasicsPlayer(name="Alice", is_you=False),
            GameBasicsPlayer(name="Bob", is_you=False),
        ),
    )

    assert (
        player_service.auto_link_from_game_basics(
            uploader_discord_id="999",
            extraction=extraction,
        )
        is None
    )


def test_resolve_or_create_polytopia_name_returns_existing_player(
    player_service: PlayerService,
) -> None:
    created = player_service.resolve_or_create_polytopia_name("Grace")
    again = player_service.resolve_or_create_polytopia_name("  GRACE  ")

    assert created.id == again.id
    assert again.polytopia_name == "Grace"
    assert again.discord_user_id is None


def test_resolve_or_create_polytopia_name_creates_unlinked_player(
    player_service: PlayerService,
    player_repo: PlayerRepo,
) -> None:
    player = player_service.resolve_or_create_polytopia_name("Henry")

    assert player.polytopia_name == "Henry"
    assert player.discord_user_id is None
    assert player.discord_display_name is None

    fetched = player_repo.get_by_polytopia_name("henry")
    assert fetched is not None
    assert fetched.id == player.id
