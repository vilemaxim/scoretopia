"""Logic-only tests for Discord interaction views (Task 012)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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


def test_can_confirm_extraction_allows_uploader_only() -> None:
    from scoretopia.discord.views import can_confirm_extraction

    assert can_confirm_extraction(uploader_discord_id="111", actor_discord_id="111")
    assert not can_confirm_extraction(
        uploader_discord_id="111",
        actor_discord_id="222",
    )


def test_extraction_confirm_view_exposes_continue_fix_abandon_buttons() -> None:
    from scoretopia.discord.views import ExtractionConfirmView

    view = ExtractionConfirmView(
        interaction_id=5,
        uploader_discord_id="111",
    )

    labels = {child.label for child in view.children}
    custom_ids = {child.custom_id for child in view.children}

    assert labels == {"Continue", "Fix", "Abandon"}
    assert view.timeout is None
    assert encode_custom_id("continue_review", interaction_id=5) in custom_ids
    assert encode_custom_id("fix_extraction", interaction_id=5) in custom_ids
    assert encode_custom_id("abandon_staged", interaction_id=5) in custom_ids


def _continue_button(view: object):
    for child in getattr(view, "children", []):
        if getattr(child, "label", None) == "Continue":
            return child
    raise AssertionError("Continue button missing")


def test_extraction_continue_disabled_until_fuzzy_and_new_slots_fix_resolved() -> None:
    """Task 034: Continue stays disabled until fuzzy/new slots are Fix-resolved."""
    from scoretopia.discord.views import ExtractionConfirmView

    resolved_roster = [
        {
            "raw_ocr": "Alice",
            "suggested_name": "Alice",
            "confidence": 1.0,
            "match_type": "exact",
        },
        {
            "raw_ocr": "Roberrt",
            "suggested_name": "Robert",
            "confidence": 0.92,
            "match_type": "fuzzy",
        },
        {
            "raw_ocr": "ZedUnknown",
            "suggested_name": None,
            "confidence": 0.0,
            "match_type": "new",
        },
    ]

    pending_view = ExtractionConfirmView(
        interaction_id=5,
        uploader_discord_id="111",
        resolved_roster=resolved_roster,
        fix_resolved_roster_slots={0: True},
    )
    assert _continue_button(pending_view).disabled is True

    ready_view = ExtractionConfirmView(
        interaction_id=5,
        uploader_discord_id="111",
        resolved_roster=resolved_roster,
        fix_resolved_roster_slots={0: True, 1: True, 2: True},
    )
    assert _continue_button(ready_view).disabled is False


def test_extraction_continue_enabled_when_all_slots_exact() -> None:
    """Task 034: exact matches need no Fix, so Continue starts enabled."""
    from scoretopia.discord.views import ExtractionConfirmView

    resolved_roster = [
        {
            "raw_ocr": "Alice",
            "suggested_name": "Alice",
            "confidence": 1.0,
            "match_type": "exact",
        },
        {
            "raw_ocr": "Bob",
            "suggested_name": "Bob",
            "confidence": 1.0,
            "match_type": "exact",
        },
    ]

    view = ExtractionConfirmView(
        interaction_id=6,
        uploader_discord_id="111",
        resolved_roster=resolved_roster,
        fix_resolved_roster_slots={0: True, 1: True},
    )
    assert _continue_button(view).disabled is False


def _require_player_link_views():
    try:
        from scoretopia.discord.views import (
            PlayerLinkRemoteConfirmView,
            PlayerSpellingConfirmView,
            can_confirm_player_link,
        )
        from scoretopia.discord.views import (
            encode_custom_id as encode_player_link_custom_id,
        )
        from scoretopia.discord.views import (
            parse_custom_id as parse_player_link_custom_id,
        )

        return (
            PlayerSpellingConfirmView,
            PlayerLinkRemoteConfirmView,
            can_confirm_player_link,
            encode_player_link_custom_id,
            parse_player_link_custom_id,
        )
    except ImportError as exc:
        pytest.fail(f"Player link views not implemented: {exc}")


def test_encode_and_parse_custom_id_includes_player_slot() -> None:
    (
        _spelling_view,
        _remote_view,
        _can_confirm,
        encode_player_link_custom_id,
        parse_player_link_custom_id,
    ) = _require_player_link_views()

    custom_id = encode_player_link_custom_id(
        "confirm_player_spelling",
        interaction_id=42,
        player_slot=2,
    )

    parsed = parse_player_link_custom_id(custom_id)

    assert parsed.action == "confirm_player_spelling"
    assert parsed.interaction_id == 42
    assert parsed.player_slot == 2


def test_player_spelling_confirm_view_custom_ids_encode_interaction_and_slot() -> None:
    (
        PlayerSpellingConfirmView,
        _remote_view,
        _can_confirm,
        encode_player_link_custom_id,
        _parse,
    ) = _require_player_link_views()

    view = PlayerSpellingConfirmView(
        interaction_id=7,
        player_slot=1,
        polytopia_name="NewBob",
        uploader_discord_id="111",
    )

    custom_ids = {child.custom_id for child in view.children}
    assert encode_player_link_custom_id(
        "confirm_player_spelling",
        interaction_id=7,
        player_slot=1,
    ) in custom_ids
    assert encode_player_link_custom_id(
        "reject_player_spelling",
        interaction_id=7,
        player_slot=1,
    ) in custom_ids


def test_player_link_remote_confirm_view_encodes_interaction_and_slot() -> None:
    (
        _spelling_view,
        PlayerLinkRemoteConfirmView,
        _can_confirm,
        encode_player_link_custom_id,
        _parse,
    ) = _require_player_link_views()

    view = PlayerLinkRemoteConfirmView(
        interaction_id=9,
        player_slot=0,
        selected_discord_user_id="222",
    )

    custom_ids = {child.custom_id for child in view.children}
    assert encode_player_link_custom_id(
        "confirm_player_link",
        interaction_id=9,
        player_slot=0,
    ) in custom_ids
    assert encode_player_link_custom_id(
        "reject_player_link",
        interaction_id=9,
        player_slot=0,
    ) in custom_ids


def test_can_confirm_player_link_allows_selected_user_only() -> None:
    (
        _spelling_view,
        _remote_view,
        can_confirm_player_link,
        _encode,
        _parse,
    ) = _require_player_link_views()

    assert can_confirm_player_link(
        selected_discord_user_id="222",
        actor_discord_id="222",
    )
    assert not can_confirm_player_link(
        selected_discord_user_id="222",
        actor_discord_id="111",
    )


def test_player_link_override_view_encodes_override_and_cancel() -> None:
    from scoretopia.discord.views import PlayerLinkOverrideView, encode_custom_id

    view = PlayerLinkOverrideView(interaction_id=11, player_slot=2)
    custom_ids = {child.custom_id for child in view.children}
    assert (
        encode_custom_id(
            "override_player_link",
            interaction_id=11,
            player_slot=2,
        )
        in custom_ids
    )
    assert (
        encode_custom_id(
            "cancel_player_link_override",
            interaction_id=11,
            player_slot=2,
        )
        in custom_ids
    )


def test_player_discord_user_select_view_includes_skip_button() -> None:
    from scoretopia.discord.views import PlayerDiscordUserSelectView, encode_custom_id

    view = PlayerDiscordUserSelectView(
        interaction_id=15,
        player_slot=2,
        uploader_discord_id="111",
    )

    custom_ids = {child.custom_id for child in view.children}
    labels = {
        getattr(child, "label", None)
        for child in view.children
        if getattr(child, "label", None) is not None
    }
    assert (
        encode_custom_id(
            "skip_player_discord",
            interaction_id=15,
            player_slot=2,
        )
        in custom_ids
    )
    assert (
        encode_custom_id(
            "select_player_discord_user",
            interaction_id=15,
            player_slot=2,
        )
        in custom_ids
    )
    assert any("skip" in label.lower() for label in labels if isinstance(label, str))
    parsed = parse_custom_id(
        encode_custom_id(
            "skip_player_discord",
            interaction_id=15,
            player_slot=2,
        )
    )
    assert parsed.action == "skip_player_discord"
    assert parsed.interaction_id == 15
    assert parsed.player_slot == 2


# --- Wrong OCR spelling — player correction pick (Task 019) ---


def _sample_player(*, player_id: int = 1, polytopia_name: str = "Alice") -> object:
    from scoretopia.storage.models import Player

    return Player(
        id=player_id,
        polytopia_name=polytopia_name,
        discord_user_id=None,
        discord_display_name=None,
    )


def _require_player_correction_views():
    try:
        from scoretopia.discord.views import (
            PlayerCorrectionPickView,
            build_player_pick_options,
        )

        return PlayerCorrectionPickView, build_player_pick_options
    except ImportError as exc:
        pytest.fail(f"Player correction pick views not implemented: {exc}")


def test_build_player_pick_options_use_player_ids_and_cap_at_twenty_five() -> None:
    _view_cls, build_player_pick_options = _require_player_correction_views()
    del _view_cls
    players = [
        _sample_player(player_id=index, polytopia_name=f"Player {index}")
        for index in range(1, 30)
    ]

    options = build_player_pick_options(players)

    assert len(options) == 25
    assert options[0].label == "Player 1"
    assert options[0].value == "1"
    assert options[-1].label == "Player 25"
    assert options[-1].value == "25"


def test_build_player_pick_options_exclude_bot_names() -> None:
    _view_cls, build_player_pick_options = _require_player_correction_views()
    del _view_cls
    players = [
        _sample_player(player_id=1, polytopia_name="Alice"),
        _sample_player(player_id=2, polytopia_name="Crazy Bot"),
        _sample_player(player_id=3, polytopia_name="Bob"),
    ]

    options = build_player_pick_options(players)

    assert [option.label for option in options] == ["Alice", "Bob"]
    assert [option.value for option in options] == ["1", "3"]


def test_player_correction_pick_view_select_custom_id_and_option_values() -> None:
    PlayerCorrectionPickView, _build_options = _require_player_correction_views()
    del _build_options
    from scoretopia.discord.views import encode_custom_id

    players = [
        _sample_player(player_id=10, polytopia_name="Alice"),
        _sample_player(player_id=20, polytopia_name="Bob"),
    ]
    view = PlayerCorrectionPickView(
        interaction_id=4,
        player_slot=2,
        players=players,
        uploader_discord_id="111",
    )

    assert len(view.children) == 1
    select = view.children[0]
    assert select.placeholder == "Pick the correct Polytopia name"
    assert select.custom_id == encode_custom_id(
        "pick_player_correction",
        interaction_id=4,
        player_slot=2,
    )
    assert [option.label for option in select.options] == ["Alice", "Bob"]
    assert [option.value for option in select.options] == ["10", "20"]


def test_can_approve_mod_batch_allows_bot_mods_only() -> None:
    from scoretopia.discord.views import can_approve_mod_batch

    assert can_approve_mod_batch(
        bot_mod_discord_ids=("111", "222"),
        actor_discord_id="111",
    )
    assert not can_approve_mod_batch(
        bot_mod_discord_ids=("111", "222"),
        actor_discord_id="333",
    )


def test_mod_approval_view_exposes_approve_and_reject_buttons() -> None:
    from scoretopia.discord.views import ModApprovalView, encode_custom_id

    view = ModApprovalView(interaction_id=30)

    labels = {child.label for child in view.children}
    custom_ids = {child.custom_id for child in view.children}

    assert labels == {"Approve", "Reject"}
    assert view.timeout is None
    assert encode_custom_id("approve_mod_batch", interaction_id=30) in custom_ids
    assert encode_custom_id("reject_mod_batch", interaction_id=30) in custom_ids


def test_final_summary_view_exposes_confirm_fix_abandon_buttons() -> None:
    from scoretopia.discord.views import FinalSummaryView, encode_custom_id

    view = FinalSummaryView(interaction_id=40)

    labels = {child.label for child in view.children}
    custom_ids = {child.custom_id for child in view.children}

    assert labels == {"Confirm", "Fix", "Abandon"}
    assert view.timeout is None
    assert encode_custom_id("confirm_final_summary", interaction_id=40) in custom_ids
    assert encode_custom_id("fix_final_summary", interaction_id=40) in custom_ids
    assert encode_custom_id("abandon_final_summary", interaction_id=40) in custom_ids


def test_can_confirm_final_summary_allows_uploader_only() -> None:
    from scoretopia.discord.views import can_confirm_final_summary

    assert can_confirm_final_summary(
        uploader_discord_id="111",
        actor_discord_id="111",
    )
    assert not can_confirm_final_summary(
        uploader_discord_id="111",
        actor_discord_id="222",
    )
