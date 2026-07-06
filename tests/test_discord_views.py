"""Logic-only tests for Discord interaction views (Task 012)."""

from __future__ import annotations

from datetime import UTC, datetime

from scoretopia.discord.views import (
    GameEndConfirmView,
    GameEndPickView,
    WinRatioConfirmView,
    build_game_pick_options,
    can_confirm_game_end,
    can_confirm_win_ratio,
    encode_custom_id,
    parse_custom_id,
    unauthorized_confirmation_message,
)
from scoretopia.storage.models import Game


def _sample_game(*, game_id: int = 1, name: str = "Friday Night") -> Game:
    return Game(
        id=game_id,
        name=name,
        status="active",
        map_size=12,
        terrain="Drylands",
        game_type="Domination",
        target_score=10000,
        game_timer="Blitz",
        winner_player_id=None,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def test_unauthorized_confirmation_message() -> None:
    assert unauthorized_confirmation_message() == "not your confirmation"


def test_can_confirm_game_end_allows_uploader_only() -> None:
    assert can_confirm_game_end(uploader_discord_id="111", actor_discord_id="111")
    assert not can_confirm_game_end(uploader_discord_id="111", actor_discord_id="222")


def test_can_confirm_win_ratio_allows_other_player_only() -> None:
    assert can_confirm_win_ratio(
        other_player_discord_id="222",
        actor_discord_id="222",
    )
    assert not can_confirm_win_ratio(
        other_player_discord_id="222",
        actor_discord_id="111",
    )


def test_build_game_pick_options_caps_at_twenty_five() -> None:
    games = [
        _sample_game(game_id=index, name=f"Game {index}")
        for index in range(1, 30)
    ]
    options = build_game_pick_options(games)

    assert len(options) == 25
    assert options[0].label == "Game 1"
    assert options[0].value == "1"
    assert options[-1].label == "Game 25"
    assert options[-1].value == "25"


def test_encode_and_parse_custom_id_round_trip() -> None:
    custom_id = encode_custom_id(
        action="confirm_game_end",
        interaction_id=42,
        game_id=7,
    )

    parsed = parse_custom_id(custom_id)

    assert parsed.action == "confirm_game_end"
    assert parsed.interaction_id == 42
    assert parsed.game_id == 7


def test_game_end_confirm_view_exposes_expected_buttons() -> None:
    view = GameEndConfirmView(interaction_id=5, game_id=9, uploader_discord_id="111")

    labels = {child.label for child in view.children}
    custom_ids = {child.custom_id for child in view.children}

    assert labels == {"Confirm", "Wrong game"}
    confirm_id = encode_custom_id("confirm_game_end", interaction_id=5, game_id=9)
    assert confirm_id in custom_ids
    assert encode_custom_id("reject_game_end", interaction_id=5) in custom_ids


def test_game_end_pick_view_exposes_select_menu() -> None:
    games = [_sample_game(game_id=1), _sample_game(game_id=2, name="Sunday")]
    view = GameEndPickView(interaction_id=3, games=games, uploader_discord_id="111")

    assert len(view.children) == 1
    select = view.children[0]
    assert select.placeholder == "Which game ended?"
    assert [option.value for option in select.options] == ["1", "2"]


def test_win_ratio_confirm_view_exposes_confirm_and_reject() -> None:
    view = WinRatioConfirmView(
        interaction_id=8,
        other_player_discord_id="222",
    )

    labels = {child.label for child in view.children}
    assert labels == {"Confirm", "Reject"}
