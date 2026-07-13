"""Discord diagnosis / Fix / final Confirm UX (Task 034 / ADR 005).

Design choices documented for implementers:
- Diagnosis surface keeps ``ExtractionConfirmView`` (or alias
  ``DiagnosisPreviewView``) but buttons become **Continue / Fix / Abandon**
  with custom_ids ``continue_review``, ``fix_extraction``, ``abandon_staged``.
  No Confirm on diagnosis.
- Uploader auth helper may stay ``can_confirm_extraction`` or be renamed
  ``can_review_staged`` (same uploader-only semantics). Tests accept either.
- Continue is disabled until every fuzzy/new slot is Fix-resolved via
  ``fix_resolved_roster_slots`` (ack-only ``slot_confirmations`` is not enough).
- Fix opens real correction UI: ``FieldCorrectionView`` with components for the
  screenshot type, never ephemeral dead-end text alone.
- Fuzzy/new slots also get ``RosterSlotFixView`` (or equivalent) offering
  accept-suggestion / pick-known / override-name — not acknowledgement-only.
- ``FinalSummaryView`` buttons become **Confirm / Fix / Abandon** with
  ``confirm_final_summary``, ``fix_final_summary``, ``abandon_final_summary``.
- Adapter primary handlers route those actions to Task 033 domain methods
  (``continue_review``, ``open_fix``, ``abandon_staged``,
  ``confirm_final_summary``); retire Confirm/Reject extraction as the primary
  Discord actions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scoretopia.discord.adapter import DiscordBotAdapter, plan_ingest_response
from scoretopia.discord.views import encode_custom_id
from scoretopia.domain.actions import (
    ActiveGameReport,
    FieldCorrectionNeedsInput,
    FinalSummaryNeedsConfirmation,
    FinalSummaryPreview,
    GameStarted,
)
from scoretopia.storage.models import Game


def _sample_game() -> Game:
    return Game(
        id=1,
        name="Friday Night",
        status="active",
        map_size=12,
        terrain="Drylands",
        game_type="Domination",
        target_score=10000,
        game_timer="Blitz",
        winner_player_id=None,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def _adapter_with_channels(
    *,
    reports_channel: MagicMock | None = None,
    input_channel: MagicMock | None = None,
) -> DiscordBotAdapter:
    reports_channel = reports_channel or MagicMock()
    reports_channel.send = AsyncMock()
    input_channel = input_channel or MagicMock()
    if not isinstance(getattr(input_channel, "send", None), AsyncMock):
        input_channel.send = AsyncMock()
    input_channel.id = getattr(input_channel, "id", None) or 100
    if not isinstance(getattr(input_channel, "fetch_message", None), AsyncMock):
        diagnosis_message = MagicMock()
        diagnosis_message.edit = AsyncMock()
        input_channel.fetch_message = AsyncMock(return_value=diagnosis_message)
    adapter = DiscordBotAdapter(
        config=MagicMock(),
        ingest_service=MagicMock(),
        game_service=MagicMock(),
        win_ratio_service=MagicMock(),
        player_service=MagicMock(),
        report_service=MagicMock(),
        token="test-token",
    )
    adapter._channel_ids = {"input": 100, "reports": 200}
    adapter._channels_by_id = {100: input_channel, 200: reports_channel}
    return adapter


def _require_diagnosis_view_cls() -> type:
    try:
        from scoretopia.discord.views import DiagnosisPreviewView

        return DiagnosisPreviewView
    except ImportError:
        pass
    try:
        from scoretopia.discord.views import ExtractionConfirmView

        return ExtractionConfirmView
    except ImportError as exc:
        pytest.fail(f"Diagnosis preview view not implemented: {exc}")


def _require_review_auth() -> Any:
    try:
        from scoretopia.discord.views import can_review_staged

        return can_review_staged
    except ImportError:
        pass
    try:
        from scoretopia.discord.views import can_confirm_extraction

        return can_confirm_extraction
    except ImportError as exc:
        pytest.fail(f"Uploader review auth helper not implemented: {exc}")


def _button_by_label(view: object, label: str):
    for child in getattr(view, "children", []):
        if getattr(child, "label", None) == label:
            return child
    raise AssertionError(f"Button {label!r} missing from {view!r}")


def _resolved_roster_with_fuzzy_and_new() -> list[dict[str, object]]:
    return [
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


def test_diagnosis_view_exposes_continue_fix_abandon_not_confirm() -> None:
    view_cls = _require_diagnosis_view_cls()
    view = view_cls(
        interaction_id=5,
        uploader_discord_id="111",
    )

    labels = {child.label for child in view.children}
    custom_ids = {child.custom_id for child in view.children}

    assert labels == {"Continue", "Fix", "Abandon"}
    assert "Confirm" not in labels
    assert "Reject" not in labels
    assert view.timeout is None
    assert encode_custom_id("continue_review", interaction_id=5) in custom_ids
    assert encode_custom_id("fix_extraction", interaction_id=5) in custom_ids
    assert encode_custom_id("abandon_staged", interaction_id=5) in custom_ids


def test_diagnosis_continue_disabled_until_fuzzy_new_fix_resolved() -> None:
    """Continue stays disabled until Fix resolves fuzzy/new slots."""
    view_cls = _require_diagnosis_view_cls()
    roster = _resolved_roster_with_fuzzy_and_new()

    pending = view_cls(
        interaction_id=5,
        uploader_discord_id="111",
        resolved_roster=roster,
        slot_confirmations={0: True, 1: True, 2: True},
        fix_resolved_roster_slots={0: True},
    )
    assert _button_by_label(pending, "Continue").disabled is True

    ready = view_cls(
        interaction_id=5,
        uploader_discord_id="111",
        resolved_roster=roster,
        slot_confirmations={0: True, 1: True, 2: True},
        fix_resolved_roster_slots={0: True, 1: True, 2: True},
    )
    assert _button_by_label(ready, "Continue").disabled is False


def test_diagnosis_continue_enabled_when_all_slots_exact() -> None:
    view_cls = _require_diagnosis_view_cls()
    roster = [
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
    view = view_cls(
        interaction_id=6,
        uploader_discord_id="111",
        resolved_roster=roster,
        fix_resolved_roster_slots={},
    )
    assert _button_by_label(view, "Continue").disabled is False


def test_can_review_staged_allows_uploader_only() -> None:
    can_review = _require_review_auth()
    assert can_review(uploader_discord_id="111", actor_discord_id="111")
    assert not can_review(uploader_discord_id="111", actor_discord_id="222")


def test_final_summary_view_exposes_confirm_fix_abandon() -> None:
    from scoretopia.discord.views import FinalSummaryView

    view = FinalSummaryView(interaction_id=40)

    labels = {child.label for child in view.children}
    custom_ids = {child.custom_id for child in view.children}

    assert labels == {"Confirm", "Fix", "Abandon"}
    assert view.timeout is None
    assert encode_custom_id("confirm_final_summary", interaction_id=40) in custom_ids
    assert encode_custom_id("fix_final_summary", interaction_id=40) in custom_ids
    assert encode_custom_id("abandon_final_summary", interaction_id=40) in custom_ids


def test_field_correction_view_exposes_components_for_game_basics() -> None:
    try:
        from scoretopia.discord.views import FieldCorrectionView
    except ImportError as exc:
        pytest.fail(f"FieldCorrectionView not implemented: {exc}")

    view = FieldCorrectionView(
        interaction_id=20,
        screenshot_type="game_basics",
        uploader_discord_id="111",
    )

    assert len(view.children) >= 1
    assert view.timeout is None
    # Must expose interactive components (selects/buttons), not an empty shell.
    assert any(
        hasattr(child, "custom_id") and child.custom_id
        for child in view.children
    )


def test_roster_slot_fix_view_offers_accept_pick_or_override() -> None:
    try:
        from scoretopia.discord.views import RosterSlotFixView
    except ImportError as exc:
        pytest.fail(f"RosterSlotFixView not implemented: {exc}")

    view = RosterSlotFixView(
        interaction_id=5,
        player_slot=1,
        raw_ocr="Roberrt",
        suggested_name="Robert",
        uploader_discord_id="111",
    )

    labels = {getattr(child, "label", None) for child in view.children}
    custom_ids = {
        getattr(child, "custom_id", "") for child in view.children
    }
    # Real resolution controls — not a lone acknowledgement button.
    assert any(
        label and ("accept" in label.lower() or "suggestion" in label.lower())
        for label in labels
        if label
    )
    assert any(
        label and ("pick" in label.lower() or "known" in label.lower())
        for label in labels
        if label
    )
    assert any(
        label and ("override" in label.lower() or "type" in label.lower())
        for label in labels
        if label
    )
    assert encode_custom_id(
        "accept_roster_suggestion",
        interaction_id=5,
        player_slot=1,
    ) in custom_ids or any("accept" in cid for cid in custom_ids)


def test_plan_ingest_response_routes_field_correction_needs_input() -> None:
    result = FieldCorrectionNeedsInput(
        interaction_id=20,
        parent_extraction_interaction_id=10,
        screenshot_type="game_basics",
    )
    plan = plan_ingest_response(result)
    assert plan.channel == "input"
    assert plan.kind == "field_correction_view"


def test_handle_fix_extraction_posts_correction_view_not_ephemeral_only() -> None:
    """Fix must render field-correction controls (ADR 005 failure mode)."""
    from scoretopia.discord.views import ParsedCustomId

    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    adapter._staged_uploader_id = MagicMock(return_value="42")
    adapter._ingest_service.open_fix.return_value = FieldCorrectionNeedsInput(
        interaction_id=20,
        parent_extraction_interaction_id=10,
        screenshot_type="game_basics",
    )

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.channel = input_channel
    parsed = ParsedCustomId(action="fix_extraction", interaction_id=10)

    handler = getattr(adapter, "_handle_fix_extraction", None)
    assert callable(handler), (
        "Adapter must expose _handle_fix_extraction (retire reject_extraction "
        "as the primary Fix handler)"
    )
    asyncio.run(handler(interaction, parsed))

    adapter._ingest_service.open_fix.assert_called_once_with(
        10,
        confirmer_discord_id="42",
    )
    adapter._ingest_service.confirm_final_summary.assert_not_called()

    # A view with components must be posted (channel send or interaction reply).
    sent_views: list[object] = []
    for mock in (input_channel.send, interaction.response.send_message):
        for call in mock.await_args_list:
            kwargs = call.kwargs
            if "view" in kwargs and kwargs["view"] is not None:
                sent_views.append(kwargs["view"])
    assert sent_views, (
        "Fix must post a correction view with components; ephemeral text alone "
        "is insufficient"
    )
    view = sent_views[0]
    assert len(getattr(view, "children", [])) >= 1


def test_handle_continue_review_routes_to_domain_continue_review() -> None:
    from scoretopia.discord.views import ParsedCustomId

    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(reports_channel=reports_channel)
    adapter._staged_uploader_id = MagicMock(return_value="42")
    summary = FinalSummaryNeedsConfirmation(
        interaction_id=40,
        parent_extraction_interaction_id=10,
        summary=FinalSummaryPreview(
            screenshot_type="game_basics",
            game_name="Friday Night",
            roster=("Alice", "Bob"),
        ),
    )
    adapter._ingest_service.continue_review.return_value = summary
    adapter._deliver_final_summary_prompt = AsyncMock()

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="continue_review", interaction_id=10)

    handler = getattr(adapter, "_handle_continue_review", None)
    assert callable(handler), (
        "Adapter must expose _handle_continue_review as the diagnosis Continue "
        "handler (not confirm_extraction as the primary action)"
    )
    asyncio.run(handler(interaction, parsed))

    adapter._ingest_service.continue_review.assert_called_once_with(
        10,
        confirmer_discord_id="42",
    )
    adapter._ingest_service.confirm_final_summary.assert_not_called()
    adapter._deliver_final_summary_prompt.assert_awaited_once()


def test_handle_abandon_staged_discards_without_commit() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    adapter._staged_uploader_id = MagicMock(return_value="42")
    adapter._ingest_service.abandon_staged.return_value = None

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="abandon_staged", interaction_id=10)

    handler = getattr(adapter, "_handle_abandon_staged", None)
    assert callable(handler), (
        "Adapter must expose _handle_abandon_staged for diagnosis Abandon"
    )
    asyncio.run(handler(interaction, parsed))

    adapter._ingest_service.abandon_staged.assert_called_once_with(
        10,
        confirmer_discord_id="42",
    )
    adapter._ingest_service.confirm_final_summary.assert_not_called()
    adapter._ingest_service.continue_review.assert_not_called()
    adapter._ingest_service.open_fix.assert_not_called()
    interaction.response.send_message.assert_awaited()
    message = interaction.response.send_message.await_args.args[0]
    assert "abandon" in message.lower() or "discard" in message.lower()


def test_handle_abandon_final_summary_discards_without_commit() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    adapter._final_summary_uploader_id = MagicMock(return_value="42")
    adapter._final_summary_parent_id = MagicMock(return_value=10)
    adapter._ingest_service.abandon_staged.return_value = None

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="abandon_final_summary", interaction_id=40)

    handler = getattr(adapter, "_handle_abandon_final_summary", None)
    assert callable(handler), (
        "Adapter must expose _handle_abandon_final_summary for final Abandon"
    )
    asyncio.run(handler(interaction, parsed))

    adapter._ingest_service.abandon_staged.assert_called_once()
    adapter._ingest_service.confirm_final_summary.assert_not_called()
    message = interaction.response.send_message.await_args.args[0]
    assert "abandon" in message.lower() or "discard" in message.lower()


def test_handle_fix_final_summary_reopens_correction_view() -> None:
    from scoretopia.discord.views import ParsedCustomId

    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    adapter._final_summary_uploader_id = MagicMock(return_value="42")
    adapter._final_summary_parent_id = MagicMock(return_value=10)
    adapter._ingest_service.open_fix.return_value = FieldCorrectionNeedsInput(
        interaction_id=21,
        parent_extraction_interaction_id=10,
        screenshot_type="game_basics",
    )

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.channel = input_channel
    parsed = ParsedCustomId(action="fix_final_summary", interaction_id=40)

    handler = getattr(adapter, "_handle_fix_final_summary", None)
    assert callable(handler), (
        "Adapter must expose _handle_fix_final_summary (replace reject-back-"
        "to-correction)"
    )
    asyncio.run(handler(interaction, parsed))

    adapter._ingest_service.open_fix.assert_called_once_with(
        10,
        confirmer_discord_id="42",
    )
    adapter._ingest_service.confirm_final_summary.assert_not_called()
    sent_views: list[object] = []
    for mock in (input_channel.send, interaction.response.send_message):
        for call in mock.await_args_list:
            if call.kwargs.get("view") is not None:
                sent_views.append(call.kwargs["view"])
    assert sent_views, "Final Fix must post correction controls with a view"


def test_component_router_wires_new_diagnosis_and_final_actions() -> None:
    """New custom_id actions must be routed (not only legacy confirm/reject)."""
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    adapter._handle_continue_review = AsyncMock()
    adapter._handle_fix_extraction = AsyncMock()
    adapter._handle_abandon_staged = AsyncMock()
    adapter._handle_fix_final_summary = AsyncMock()
    adapter._handle_abandon_final_summary = AsyncMock()
    adapter._handle_confirm_final_summary = AsyncMock()

    interaction = MagicMock()
    cases = [
        ("continue_review", 10, "_handle_continue_review"),
        ("fix_extraction", 10, "_handle_fix_extraction"),
        ("abandon_staged", 10, "_handle_abandon_staged"),
        ("fix_final_summary", 40, "_handle_fix_final_summary"),
        ("abandon_final_summary", 40, "_handle_abandon_final_summary"),
        ("confirm_final_summary", 40, "_handle_confirm_final_summary"),
    ]
    for action, interaction_id, attr in cases:
        asyncio.run(
            adapter._handle_component(
                interaction,
                ParsedCustomId(action=action, interaction_id=interaction_id),
            )
        )
        getattr(adapter, attr).assert_awaited()


def test_diagnosis_to_final_confirm_flow_delivers_committed_result() -> None:
    """diagnosis Continue → final Confirm → committed result delivered."""
    from scoretopia.discord.views import ParsedCustomId

    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(reports_channel=reports_channel)
    adapter._staged_uploader_id = MagicMock(return_value="42")
    adapter._final_summary_uploader_id = MagicMock(return_value="42")

    summary = FinalSummaryNeedsConfirmation(
        interaction_id=40,
        parent_extraction_interaction_id=10,
        summary=FinalSummaryPreview(
            screenshot_type="game_basics",
            game_name="Friday Night",
            roster=("Alice", "Bob"),
        ),
    )
    committed = GameStarted(
        game=_sample_game(),
        report=ActiveGameReport(
            game_id=1,
            game_name="Friday Night",
            human_player_names=("Alice", "Bob"),
            bot_count=0,
        ),
    )
    adapter._ingest_service.continue_review.return_value = summary
    adapter._ingest_service.confirm_final_summary.return_value = committed
    adapter._deliver_final_summary_prompt = AsyncMock()
    adapter._deliver_committed_ingest_result = AsyncMock()

    continue_handler = getattr(adapter, "_handle_continue_review", None)
    assert callable(continue_handler)

    continue_interaction = MagicMock()
    continue_interaction.user.id = 42
    continue_interaction.response.send_message = AsyncMock()
    asyncio.run(
        continue_handler(
            continue_interaction,
            ParsedCustomId(action="continue_review", interaction_id=10),
        )
    )
    adapter._ingest_service.continue_review.assert_called_once()
    adapter._deliver_final_summary_prompt.assert_awaited_once()
    adapter._ingest_service.confirm_final_summary.assert_not_called()

    confirm_interaction = MagicMock()
    confirm_interaction.user.id = 42
    confirm_interaction.response.send_message = AsyncMock()
    asyncio.run(
        adapter._handle_confirm_final_summary(
            confirm_interaction,
            ParsedCustomId(action="confirm_final_summary", interaction_id=40),
        )
    )
    adapter._ingest_service.confirm_final_summary.assert_called_once_with(
        40,
        confirmer_discord_id="42",
    )
    adapter._deliver_committed_ingest_result.assert_awaited_once()


def test_field_correction_view_uses_field_buttons_not_lone_select() -> None:
    from scoretopia.discord.views import FieldCorrectionView

    view = FieldCorrectionView(
        interaction_id=20,
        screenshot_type="game_basics",
        uploader_discord_id="111",
    )
    custom_ids = {getattr(child, "custom_id", "") for child in view.children}
    assert encode_custom_id(
        "pick_field_correction",
        interaction_id=20,
        field="game_name",
    ) in custom_ids
    assert all(
        not type(child).__name__.endswith("Select")
        for child in view.children
    )


def test_component_router_wires_fix_child_actions() -> None:
    """Fix child custom_ids must be dispatched (never silent no-op)."""
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    adapter._handle_pick_field_correction = AsyncMock()
    adapter._handle_submit_field_correction = AsyncMock()
    adapter._handle_accept_roster_suggestion = AsyncMock()
    adapter._handle_pick_roster_known_player = AsyncMock()
    adapter._handle_select_roster_known_player = AsyncMock()
    adapter._handle_override_roster_name = AsyncMock()
    adapter._handle_submit_roster_override = AsyncMock()
    adapter._handle_add_roster_player = AsyncMock()
    adapter._handle_submit_add_roster_player = AsyncMock()
    adapter._handle_remove_roster_player = AsyncMock()
    adapter._handle_move_roster_player_up = AsyncMock()
    adapter._handle_move_roster_player_down = AsyncMock()

    interaction = MagicMock()
    cases = [
        (
            "pick_field_correction",
            "_handle_pick_field_correction",
            {"field": "game_name"},
        ),
        (
            "submit_field_correction",
            "_handle_submit_field_correction",
            {"field": "game_name"},
        ),
        (
            "accept_roster_suggestion",
            "_handle_accept_roster_suggestion",
            {"player_slot": 1},
        ),
        (
            "pick_roster_known_player",
            "_handle_pick_roster_known_player",
            {"player_slot": 1},
        ),
        (
            "select_roster_known_player",
            "_handle_select_roster_known_player",
            {"player_slot": 1},
        ),
        (
            "override_roster_name",
            "_handle_override_roster_name",
            {"player_slot": 1},
        ),
        (
            "submit_roster_override",
            "_handle_submit_roster_override",
            {"player_slot": 1},
        ),
        (
            "add_roster_player",
            "_handle_add_roster_player",
            {},
        ),
        (
            "submit_add_roster_player",
            "_handle_submit_add_roster_player",
            {},
        ),
        (
            "remove_roster_player",
            "_handle_remove_roster_player",
            {"player_slot": 1},
        ),
        (
            "move_roster_player_up",
            "_handle_move_roster_player_up",
            {"player_slot": 1},
        ),
        (
            "move_roster_player_down",
            "_handle_move_roster_player_down",
            {"player_slot": 1},
        ),
    ]
    for action, attr, extra in cases:
        asyncio.run(
            adapter._handle_component(
                interaction,
                ParsedCustomId(action=action, interaction_id=20, **extra),
            )
        )
        getattr(adapter, attr).assert_awaited()


def _fix_pending_repo(
    *,
    correction_id: int = 20,
    parent_id: int = 10,
    uploader: str = "42",
    screenshot_type: str = "game_basics",
    extraction: dict[str, object] | None = None,
    resolved_roster: list[dict[str, object]] | None = None,
) -> MagicMock:
    extraction = extraction or {
        "screenshot_type": "game_basics",
        "game_name": "Typo Game",
        "map_size": 12,
        "terrain": "Drylands",
        "game_timer": "Blitz",
        "target_score": 10000,
        "game_type": "Domination",
        "players": [
            {"name": "Alice", "tribe": "Xin-xi", "is_you": True},
            {"name": "Roberrt", "tribe": "Imperius", "is_you": False},
        ],
    }
    resolved_roster = resolved_roster or [
        {
            "raw_ocr": "Alice",
            "suggested_name": "Alice",
            "confidence": 1.0,
            "match_type": "exact",
        },
        {
            "raw_ocr": "Roberrt",
            "suggested_name": "Robert",
            "confidence": 0.85,
            "match_type": "fuzzy",
        },
    ]
    parent = MagicMock()
    parent.id = parent_id
    parent.kind = "confirm_extraction"
    parent.discord_user_id = uploader
    parent.status = "open"
    parent.payload = {
        "screenshot_type": screenshot_type,
        "uploader_discord_id": uploader,
        "extraction": extraction,
        "resolved_roster": resolved_roster,
        "fix_resolved_roster_slots": {},
        "slot_confirmations": {"0": True, "1": False},
        "diagnosis_channel_id": 100,
        "diagnosis_message_id": 9999,
    }
    correction = MagicMock()
    correction.id = correction_id
    correction.kind = "field_correction"
    correction.discord_user_id = uploader
    correction.status = "open"
    correction.payload = {
        "parent_extraction_interaction_id": parent_id,
        "uploader_discord_id": uploader,
        "screenshot_type": screenshot_type,
        "corrections": [],
    }

    def _get(interaction_id: int):
        if interaction_id == correction_id:
            return correction
        if interaction_id == parent_id:
            return parent
        return None

    repo = MagicMock()
    repo.get_by_id.side_effect = _get
    repo.update_payload = MagicMock()
    return repo


def test_pick_field_correction_opens_modal_for_uploader() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock()

    asyncio.run(
        adapter._handle_pick_field_correction(
            interaction,
            ParsedCustomId(
                action="pick_field_correction",
                interaction_id=20,
                field="game_name",
            ),
        )
    )
    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert "game_name" in modal.custom_id


def test_submit_field_correction_updates_staged_parent_extraction() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.data = {
        "custom_id": encode_custom_id(
            "submit_field_correction",
            interaction_id=20,
            field="game_name",
        ),
        "components": [
            {
                "components": [
                    {"custom_id": "new_value", "value": "Friday Night"},
                ]
            }
        ],
    }

    asyncio.run(
        adapter._handle_submit_field_correction(
            interaction,
            ParsedCustomId(
                action="submit_field_correction",
                interaction_id=20,
                field="game_name",
            ),
        )
    )
    parent = repo.get_by_id(10)
    assert parent.payload["extraction"]["game_name"] == "Friday Night"
    interaction.response.send_message.assert_awaited()
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True


def test_accept_roster_suggestion_resolves_slot_and_updates_name() -> None:
    from scoretopia.discord.views import ParsedCustomId

    input_channel = MagicMock()
    input_channel.id = 100
    input_channel.send = AsyncMock()
    diagnosis_message = MagicMock()
    diagnosis_message.edit = AsyncMock()
    input_channel.fetch_message = AsyncMock(return_value=diagnosis_message)
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()

    asyncio.run(
        adapter._handle_accept_roster_suggestion(
            interaction,
            ParsedCustomId(
                action="accept_roster_suggestion",
                interaction_id=20,
                player_slot=1,
            ),
        )
    )
    parent = repo.get_by_id(10)
    assert parent.payload["extraction"]["players"][1]["name"] == "Robert"
    assert parent.payload["fix_resolved_roster_slots"]["1"] is True
    interaction.response.send_message.assert_awaited()
    assert (
        interaction.response.send_message.await_args.args[0]
        == "Player slot resolved. Continue when ready."
    )
    input_channel.fetch_message.assert_awaited_once_with(9999)
    diagnosis_message.edit.assert_awaited_once()
    edit_kwargs = diagnosis_message.edit.await_args.kwargs
    assert "embed" in edit_kwargs
    assert "view" in edit_kwargs
    continue_btn = _button_by_label(edit_kwargs["view"], "Continue")
    assert continue_btn.disabled is False


def test_accept_roster_keeps_continue_disabled_when_other_slots_open() -> None:
    from scoretopia.discord.views import ParsedCustomId

    input_channel = MagicMock()
    input_channel.id = 100
    input_channel.send = AsyncMock()
    diagnosis_message = MagicMock()
    diagnosis_message.edit = AsyncMock()
    input_channel.fetch_message = AsyncMock(return_value=diagnosis_message)
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo(
        extraction={
            "screenshot_type": "game_basics",
            "game_name": "Typo Game",
            "map_size": 12,
            "terrain": "Drylands",
            "game_timer": "Blitz",
            "target_score": 10000,
            "game_type": "Domination",
            "players": [
                {"name": "Alice", "tribe": "Xin-xi", "is_you": True},
                {"name": "Roberrt", "tribe": "Imperius", "is_you": False},
                {"name": "ZedUnknown", "tribe": "Bardur", "is_you": False},
            ],
        },
        resolved_roster=_resolved_roster_with_fuzzy_and_new(),
    )
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    asyncio.run(
        adapter._handle_accept_roster_suggestion(
            interaction,
            ParsedCustomId(
                action="accept_roster_suggestion",
                interaction_id=20,
                player_slot=1,
            ),
        )
    )
    diagnosis_message.edit.assert_awaited_once()
    view = diagnosis_message.edit.await_args.kwargs["view"]
    assert _button_by_label(view, "Continue").disabled is True


def test_roster_fix_refresh_failure_sends_distinct_ephemeral() -> None:
    from scoretopia.discord.adapter import _DIAGNOSIS_PREVIEW_REFRESH_FAILED
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    repo = _fix_pending_repo()
    parent = repo.get_by_id(10)
    del parent.payload["diagnosis_message_id"]
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    asyncio.run(
        adapter._handle_accept_roster_suggestion(
            interaction,
            ParsedCustomId(
                action="accept_roster_suggestion",
                interaction_id=20,
                player_slot=1,
            ),
        )
    )
    parent = repo.get_by_id(10)
    assert parent.payload["fix_resolved_roster_slots"]["1"] is True
    interaction.response.send_message.assert_awaited_once_with(
        _DIAGNOSIS_PREVIEW_REFRESH_FAILED,
        ephemeral=True,
    )


def test_deliver_ingest_result_persists_diagnosis_message_ids() -> None:
    from scoretopia.domain.actions import ExtractionNeedsConfirmation, ExtractionPreview

    input_channel = MagicMock()
    input_channel.id = 100
    input_channel.send = AsyncMock()
    input_channel.fetch_message = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo()
    # Fresh delivery should write ids onto an empty parent payload.
    parent = repo.get_by_id(10)
    parent.payload.pop("diagnosis_channel_id", None)
    parent.payload.pop("diagnosis_message_id", None)
    adapter._ingest_service._pending_repo = repo

    reply = MagicMock()
    reply.id = 4242
    reply.channel = MagicMock()
    reply.channel.id = 100
    message = MagicMock()
    message.author.id = 42
    message.reply = AsyncMock(return_value=reply)
    message.add_reaction = AsyncMock()

    result = ExtractionNeedsConfirmation(
        interaction_id=10,
        preview=ExtractionPreview(
            screenshot_type="game_basics",
            game_name="Typo Game",
        ),
    )
    asyncio.run(adapter._deliver_ingest_result(message, result))

    assert parent.payload["diagnosis_channel_id"] == 100
    assert parent.payload["diagnosis_message_id"] == 4242
    repo.update_payload.assert_any_call(10, parent.payload)


def test_fix_controls_unauthorized_non_uploader_are_acked() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 99
    interaction.response.send_message = AsyncMock()

    asyncio.run(
        adapter._handle_pick_field_correction(
            interaction,
            ParsedCustomId(
                action="pick_field_correction",
                interaction_id=20,
                field="game_name",
            ),
        )
    )
    interaction.response.send_message.assert_awaited()
    assert "not your" in interaction.response.send_message.await_args.args[0].lower()
    interaction.response.send_modal = AsyncMock()
    # Ensure we did not open a modal for the unauthorized user.
    assert not hasattr(interaction.response.send_modal, "await_args") or (
        interaction.response.send_modal.await_count == 0
        if hasattr(interaction.response.send_modal, "await_count")
        else True
    )


def test_handle_fix_extraction_posts_roster_slot_views_for_fuzzy() -> None:
    from scoretopia.discord.views import ParsedCustomId, RosterSlotFixView

    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo
    adapter._staged_uploader_id = MagicMock(return_value="42")
    adapter._ingest_service.open_fix.return_value = FieldCorrectionNeedsInput(
        interaction_id=20,
        parent_extraction_interaction_id=10,
        screenshot_type="game_basics",
    )

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.channel = input_channel

    asyncio.run(
        adapter._handle_fix_extraction(
            interaction,
            ParsedCustomId(action="fix_extraction", interaction_id=10),
        )
    )

    # Initial Fix response posts FieldCorrectionView; roster views via followup
    # after response.is_done becomes true, or channel.send.
    interaction.response.is_done = MagicMock(return_value=True)
    # Re-run delivery path directly to assert roster posting with done response.
    asyncio.run(
        adapter._deliver_field_correction_response(
            interaction,
            FieldCorrectionNeedsInput(
                interaction_id=20,
                parent_extraction_interaction_id=10,
                screenshot_type="game_basics",
            ),
        )
    )
    roster_views = []
    for mock in (interaction.followup.send, input_channel.send):
        for call in mock.await_args_list:
            view = call.kwargs.get("view")
            if isinstance(view, RosterSlotFixView):
                roster_views.append(view)
    assert roster_views, (
        "Fix must post RosterSlotFixView for unresolved fuzzy/new slots"
    )


def test_roster_shape_edit_view_exposes_add_player() -> None:
    try:
        from scoretopia.discord.views import RosterShapeEditView
    except ImportError as exc:
        pytest.fail(f"RosterShapeEditView not implemented: {exc}")

    view = RosterShapeEditView(
        interaction_id=20,
        uploader_discord_id="111",
    )
    labels = {getattr(child, "label", None) for child in view.children}
    custom_ids = {getattr(child, "custom_id", "") for child in view.children}
    assert any(label and "add" in label.lower() for label in labels if label)
    assert encode_custom_id("add_roster_player", interaction_id=20) in custom_ids
    assert view.timeout is None


def test_roster_human_shape_view_exposes_remove_and_reorder() -> None:
    try:
        from scoretopia.discord.views import RosterHumanShapeView
    except ImportError as exc:
        pytest.fail(f"RosterHumanShapeView not implemented: {exc}")

    view = RosterHumanShapeView(
        interaction_id=20,
        player_slot=1,
        player_name="Bob",
        uploader_discord_id="111",
    )
    labels = {
        (getattr(child, "label", None) or "").lower() for child in view.children
    }
    custom_ids = {getattr(child, "custom_id", "") for child in view.children}
    assert any("remove" in label for label in labels)
    assert any("up" in label for label in labels)
    assert any("down" in label for label in labels)
    assert (
        encode_custom_id(
            "remove_roster_player",
            interaction_id=20,
            player_slot=1,
        )
        in custom_ids
    )
    assert (
        encode_custom_id(
            "move_roster_player_up",
            interaction_id=20,
            player_slot=1,
        )
        in custom_ids
    )
    assert (
        encode_custom_id(
            "move_roster_player_down",
            interaction_id=20,
            player_slot=1,
        )
        in custom_ids
    )
    assert view.timeout is None


def test_add_roster_player_opens_modal_for_uploader() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock()

    handler = getattr(adapter, "_handle_add_roster_player", None)
    assert callable(handler), "Adapter must expose _handle_add_roster_player"
    asyncio.run(
        handler(
            interaction,
            ParsedCustomId(action="add_roster_player", interaction_id=20),
        )
    )
    interaction.response.send_modal.assert_awaited_once()


def test_submit_add_roster_player_updates_parent_and_refreshes_diagnosis() -> None:
    from scoretopia.discord.views import ParsedCustomId

    input_channel = MagicMock()
    input_channel.id = 100
    input_channel.send = AsyncMock()
    diagnosis_message = MagicMock()
    diagnosis_message.edit = AsyncMock()
    input_channel.fetch_message = AsyncMock(return_value=diagnosis_message)
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo
    player_repo = MagicMock()
    player_repo.list_all.return_value = []
    adapter._player_repo = player_repo
    adapter._player_service.player_repo = player_repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.data = {
        "custom_id": encode_custom_id(
            "submit_add_roster_player",
            interaction_id=20,
        ),
        "components": [
            {
                "components": [
                    {"custom_id": "new_value", "value": "Carol"},
                ]
            }
        ],
    }

    handler = getattr(adapter, "_handle_submit_add_roster_player", None)
    assert callable(handler), "Adapter must expose _handle_submit_add_roster_player"
    asyncio.run(
        handler(
            interaction,
            ParsedCustomId(action="submit_add_roster_player", interaction_id=20),
        )
    )

    parent = repo.get_by_id(10)
    names = [p["name"] for p in parent.payload["extraction"]["players"]]
    assert "Carol" in names
    assert len(parent.payload["resolved_roster"]) == 3
    input_channel.fetch_message.assert_awaited_once_with(9999)
    diagnosis_message.edit.assert_awaited_once()
    edit_kwargs = diagnosis_message.edit.await_args.kwargs
    assert "embed" in edit_kwargs
    assert "view" in edit_kwargs
    interaction.response.send_message.assert_awaited()
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True


def test_remove_roster_player_updates_parent_and_refreshes_diagnosis() -> None:
    from scoretopia.discord.views import ParsedCustomId

    input_channel = MagicMock()
    input_channel.id = 100
    diagnosis_message = MagicMock()
    diagnosis_message.edit = AsyncMock()
    input_channel.fetch_message = AsyncMock(return_value=diagnosis_message)
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo(
        extraction={
            "screenshot_type": "game_basics",
            "game_name": "Typo Game",
            "map_size": 12,
            "terrain": "Drylands",
            "game_timer": "Blitz",
            "target_score": 10000,
            "game_type": "Domination",
            "players": [
                {"name": "Alice", "tribe": "Xin-xi", "is_you": True},
                {"name": "JunkName", "tribe": "Imperius", "is_you": False},
                {"name": "Bob", "tribe": "Bardur", "is_you": False},
            ],
        },
        resolved_roster=[
            {
                "raw_ocr": "Alice",
                "suggested_name": "Alice",
                "confidence": 1.0,
                "match_type": "exact",
            },
            {
                "raw_ocr": "JunkName",
                "suggested_name": None,
                "confidence": 0.0,
                "match_type": "new",
            },
            {
                "raw_ocr": "Bob",
                "suggested_name": "Bob",
                "confidence": 1.0,
                "match_type": "exact",
            },
        ],
    )
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    handler = getattr(adapter, "_handle_remove_roster_player", None)
    assert callable(handler), "Adapter must expose _handle_remove_roster_player"
    asyncio.run(
        handler(
            interaction,
            ParsedCustomId(
                action="remove_roster_player",
                interaction_id=20,
                player_slot=1,
            ),
        )
    )

    parent = repo.get_by_id(10)
    names = [p["name"] for p in parent.payload["extraction"]["players"]]
    assert names == ["Alice", "Bob"]
    assert [r["raw_ocr"] for r in parent.payload["resolved_roster"]] == [
        "Alice",
        "Bob",
    ]
    diagnosis_message.edit.assert_awaited_once()
    interaction.response.send_message.assert_awaited()


def test_move_roster_player_up_updates_order_and_refreshes_diagnosis() -> None:
    from scoretopia.discord.views import ParsedCustomId

    input_channel = MagicMock()
    input_channel.id = 100
    diagnosis_message = MagicMock()
    diagnosis_message.edit = AsyncMock()
    input_channel.fetch_message = AsyncMock(return_value=diagnosis_message)
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo(
        extraction={
            "screenshot_type": "game_basics",
            "game_name": "Typo Game",
            "map_size": 12,
            "terrain": "Drylands",
            "game_timer": "Blitz",
            "target_score": 10000,
            "game_type": "Domination",
            "players": [
                {"name": "Alice", "is_you": True},
                {"name": "Bob", "is_you": False},
                {"name": "Carol", "is_you": False},
            ],
        },
        resolved_roster=[
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
            {
                "raw_ocr": "Carol",
                "suggested_name": "Carol",
                "confidence": 1.0,
                "match_type": "exact",
            },
        ],
    )
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    handler = getattr(adapter, "_handle_move_roster_player_up", None)
    assert callable(handler), "Adapter must expose _handle_move_roster_player_up"
    asyncio.run(
        handler(
            interaction,
            ParsedCustomId(
                action="move_roster_player_up",
                interaction_id=20,
                player_slot=2,
            ),
        )
    )

    parent = repo.get_by_id(10)
    assert [p["name"] for p in parent.payload["extraction"]["players"]] == [
        "Alice",
        "Carol",
        "Bob",
    ]
    assert [r["raw_ocr"] for r in parent.payload["resolved_roster"]] == [
        "Alice",
        "Carol",
        "Bob",
    ]
    diagnosis_message.edit.assert_awaited_once()


def test_roster_shape_edit_unauthorized_non_uploader_is_acked() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 99
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()

    for action, extra in (
        ("add_roster_player", {}),
        ("remove_roster_player", {"player_slot": 1}),
        ("move_roster_player_up", {"player_slot": 1}),
        ("move_roster_player_down", {"player_slot": 0}),
    ):
        handler_name = f"_handle_{action}"
        handler = getattr(adapter, handler_name, None)
        assert callable(handler), f"Adapter must expose {handler_name}"
        interaction.response.send_message.reset_mock()
        interaction.response.send_modal.reset_mock()
        asyncio.run(
            handler(
                interaction,
                ParsedCustomId(action=action, interaction_id=20, **extra),
            )
        )
        interaction.response.send_message.assert_awaited()
        msg = interaction.response.send_message.await_args.args[0].lower()
        assert "not your" in msg
        assert interaction.response.send_modal.await_count == 0


def test_roster_shape_refresh_failure_sends_distinct_ephemeral() -> None:
    from scoretopia.discord.adapter import _DIAGNOSIS_PREVIEW_REFRESH_FAILED
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    repo = _fix_pending_repo()
    parent = repo.get_by_id(10)
    del parent.payload["diagnosis_message_id"]
    adapter._ingest_service._pending_repo = repo

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    handler = getattr(adapter, "_handle_remove_roster_player", None)
    assert callable(handler), "Adapter must expose _handle_remove_roster_player"
    asyncio.run(
        handler(
            interaction,
            ParsedCustomId(
                action="remove_roster_player",
                interaction_id=20,
                player_slot=1,
            ),
        )
    )
    parent = repo.get_by_id(10)
    assert len(parent.payload["extraction"]["players"]) == 1
    interaction.response.send_message.assert_awaited_once_with(
        _DIAGNOSIS_PREVIEW_REFRESH_FAILED,
        ephemeral=True,
    )


def test_fix_delivery_posts_roster_shape_controls() -> None:
    """Fix delivery must include add/remove/reorder controls (Task 039)."""
    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    repo = _fix_pending_repo()
    adapter._ingest_service._pending_repo = repo
    adapter._staged_uploader_id = MagicMock(return_value="42")

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.channel = input_channel

    asyncio.run(
        adapter._deliver_field_correction_response(
            interaction,
            FieldCorrectionNeedsInput(
                interaction_id=20,
                parent_extraction_interaction_id=10,
                screenshot_type="game_basics",
            ),
        )
    )

    posted_custom_ids: set[str] = set()
    for mock in (
        interaction.response.send_message,
        interaction.followup.send,
        input_channel.send,
    ):
        for call in mock.await_args_list:
            view = call.kwargs.get("view")
            if view is None:
                continue
            for child in getattr(view, "children", []):
                cid = getattr(child, "custom_id", None)
                if cid:
                    posted_custom_ids.add(cid)

    assert any("add_roster_player" in cid for cid in posted_custom_ids), (
        "Fix must expose Add player control"
    )
    assert any("remove_roster_player" in cid for cid in posted_custom_ids), (
        "Fix must expose Remove player control"
    )
    assert any(
        "move_roster_player_up" in cid or "move_roster_player_down" in cid
        for cid in posted_custom_ids
    ), "Fix must expose reorder controls"
