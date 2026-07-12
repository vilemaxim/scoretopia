"""Tests for Discord bot adapter helpers (Task 012)."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scoretopia.config import ChannelsConfig
from scoretopia.discord.adapter import (
    DiscordBotAdapter,
    DiscordConfigError,
    load_discord_token,
    plan_dispute_response,
    plan_ingest_response,
    resolve_guild_channels,
)
from scoretopia.discord.views import ParsedCustomId
from scoretopia.domain.actions import (
    ActiveGameReport,
    ExtractionNeedsConfirmation,
    ExtractionPreview,
    GameEndNeedsConfirmation,
    GameEndNeedsPick,
    GameEndPendingStart,
    GameStarted,
    IngestError,
    UnrecognizedScreenshot,
    WinRatioNeedsConfirmation,
)
from scoretopia.domain.win_ratios import DisputeResult
from scoretopia.ports.bot import BotPort
from scoretopia.screenshot.models import GameBasicsExtraction, GameBasicsPlayer
from scoretopia.storage.models import Game


def _fix_resolve_unresolved_roster_slots_for_tests(
    pending_repo: object,
    parent_interaction_id: int,
) -> None:
    from scoretopia.domain.player_resolution import (
        mark_all_unresolved_roster_slots_fix_resolved,
    )

    pending = pending_repo.get_by_id(parent_interaction_id)  # type: ignore[attr-defined]
    assert pending is not None
    mark_all_unresolved_roster_slots_fix_resolved(pending.payload)
    pending_repo.update_payload(parent_interaction_id, pending.payload)  # type: ignore[attr-defined]


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


@dataclass(frozen=True)
class _FakeTextChannel:
    name: str
    id: int


def test_bot_port_protocol_requires_run() -> None:
    assert hasattr(BotPort, "run")


def test_load_discord_token_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCORETOPIA_DISCORD_TOKEN", "test-token-value")
    assert load_discord_token() == "test-token-value"


def test_load_discord_token_fails_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCORETOPIA_DISCORD_TOKEN", raising=False)
    with pytest.raises(DiscordConfigError, match="SCORETOPIA_DISCORD_TOKEN"):
        load_discord_token()


def test_resolve_guild_channels_maps_configured_names() -> None:
    guild = MagicMock()
    guild.text_channels = [
        _FakeTextChannel(name="polytopia-screenshots", id=100),
        _FakeTextChannel(name="polytopia-reports", id=200),
        _FakeTextChannel(name="general", id=300),
    ]
    channels = ChannelsConfig(
        input="polytopia-screenshots",
        reports="polytopia-reports",
    )

    resolved = resolve_guild_channels(guild, channels)

    assert resolved == {"input": 100, "reports": 200}


def test_resolve_guild_channels_fails_fast_when_channel_missing() -> None:
    guild = MagicMock()
    guild.text_channels = [_FakeTextChannel(name="polytopia-screenshots", id=100)]
    channels = ChannelsConfig(input="polytopia-screenshots", reports="missing-channel")

    with pytest.raises(DiscordConfigError, match="missing-channel"):
        resolve_guild_channels(guild, channels)


@pytest.mark.parametrize(
    ("build_result", "expected_channel", "expected_kind"),
    [
        (
            lambda: GameStarted(
                game=_sample_game(),
                report=ActiveGameReport(
                    game_id=1,
                    game_name="Friday Night",
                    human_player_names=("Alice", "Bob"),
                    bot_count=0,
                ),
            ),
            "reports",
            "embed",
        ),
        (
            lambda: GameEndNeedsConfirmation(game_id=1, interaction_id=10),
            "input",
            "game_end_confirm_view",
        ),
        (
            lambda: GameEndNeedsPick(game_ids=(1, 2), interaction_id=11),
            "input",
            "game_end_pick_view",
        ),
        (
            lambda: GameEndPendingStart(interaction_id=12),
            "input",
            "pending_start_reply",
        ),
        (
            lambda: WinRatioNeedsConfirmation(other_player_id=5, interaction_id=13),
            "input",
            "win_ratio_confirm_view",
        ),
        (
            lambda: UnrecognizedScreenshot(
                message="Could not recognize this screenshot."
            ),
            "input",
            "guidance_reply",
        ),
    ],
)
def test_plan_ingest_response_routes_by_action_type(
    build_result: object,
    expected_channel: str,
    expected_kind: str,
) -> None:
    result = build_result()
    plan = plan_ingest_response(result)

    assert plan.channel == expected_channel
    assert plan.kind == expected_kind


def test_plan_dispute_response_targets_input_channel() -> None:
    dispute = DisputeResult(
        dispute_id=99,
        message="Win-ratio dispute: Alice claimed 9–11 vs Bob.",
    )

    plan = plan_dispute_response(dispute)

    assert plan.channel == "input"
    assert plan.kind == "dispute_embed"
    assert dispute.message in plan.body


def test_discord_bot_adapter_implements_bot_port() -> None:
    adapter = DiscordBotAdapter(
        config=MagicMock(),
        ingest_service=MagicMock(),
        game_service=MagicMock(),
        win_ratio_service=MagicMock(),
        player_service=MagicMock(),
        report_service=MagicMock(),
        token="test-token",
    )

    assert isinstance(adapter, BotPort)


def test_screenshot_upload_runs_ocr_off_event_loop(tmp_path: Path) -> None:
    loop_thread_id = threading.get_ident()
    extract_thread_id: int | None = None
    stored_path = tmp_path / "shot.png"

    def extract_stored(_path: Path) -> UnrecognizedScreenshot:
        nonlocal extract_thread_id
        extract_thread_id = threading.get_ident()
        return UnrecognizedScreenshot(message="Could not recognize this screenshot.")

    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.side_effect = extract_stored

    config = MagicMock()
    config.inbox.path = tmp_path

    adapter = DiscordBotAdapter(
        config=config,
        ingest_service=ingest_service,
        game_service=MagicMock(),
        win_ratio_service=MagicMock(),
        player_service=MagicMock(),
        report_service=MagicMock(),
        token="test-token",
    )
    adapter._deliver_ingest_result = AsyncMock()

    message = MagicMock()
    message.author.id = 42
    attachment = MagicMock()
    attachment.filename = "shot.png"
    attachment.save = AsyncMock()

    asyncio.run(adapter._handle_screenshot_upload(message, attachment))

    assert extract_thread_id is not None
    assert extract_thread_id != loop_thread_id
    ingest_service.prepare_stored_path.assert_called_once_with(stored_path)
    ingest_service.extract_stored_screenshot.assert_called_once_with(stored_path)
    ingest_service.process_extracted_screenshot.assert_not_called()
    ingest_service.report_extraction_failure.assert_called_once()
    adapter._deliver_ingest_result.assert_awaited_once()


def test_screenshot_upload_stages_ingest_on_event_loop(tmp_path: Path) -> None:
    loop_thread_id = threading.get_ident()
    stage_thread_id: int | None = None
    stored_path = tmp_path / "end.png"
    extraction = MagicMock()
    expected = _extraction_needs_confirmation()

    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.return_value = extraction

    def stage_screenshot(
        _stored_path: Path,
        *,
        uploader_discord_id: str,
        filename: str,
    ) -> ExtractionNeedsConfirmation:
        nonlocal stage_thread_id
        stage_thread_id = threading.get_ident()
        assert uploader_discord_id == "99"
        assert filename == "end.png"
        return expected

    ingest_service.stage_screenshot.side_effect = stage_screenshot

    config = MagicMock()
    config.inbox.path = tmp_path

    adapter = DiscordBotAdapter(
        config=config,
        ingest_service=ingest_service,
        game_service=MagicMock(),
        win_ratio_service=MagicMock(),
        player_service=MagicMock(),
        report_service=MagicMock(),
        token="test-token",
    )
    adapter._deliver_ingest_result = AsyncMock()

    message = MagicMock()
    message.author.id = 99
    attachment = MagicMock()
    attachment.filename = "end.png"
    attachment.save = AsyncMock()

    asyncio.run(adapter._handle_screenshot_upload(message, attachment))

    assert stage_thread_id == loop_thread_id
    ingest_service.stage_screenshot.assert_called_once_with(
        stored_path,
        uploader_discord_id="99",
        filename="end.png",
    )
    adapter._deliver_ingest_result.assert_awaited_once_with(message, expected)


_PROCESSING_REACTION = "👀"
_SUCCESS_REACTION = "👍"
_FAILURE_REACTION = "❌"


def _screenshot_adapter(tmp_path: Path, ingest_service: MagicMock) -> DiscordBotAdapter:
    config = MagicMock()
    config.inbox.path = tmp_path
    return DiscordBotAdapter(
        config=config,
        ingest_service=ingest_service,
        game_service=MagicMock(),
        win_ratio_service=MagicMock(),
        player_service=MagicMock(),
        report_service=MagicMock(),
        token="test-token",
    )


def _upload_message(*, message_id: int = 1001) -> MagicMock:
    message = MagicMock()
    message.id = message_id
    message.author.id = 42
    message.reactions = []
    message.add_reaction = AsyncMock()
    message.remove_reaction = AsyncMock()
    message.reply = AsyncMock()
    return message


def _upload_attachment(filename: str = "shot.png") -> MagicMock:
    attachment = MagicMock()
    attachment.filename = filename
    attachment.save = AsyncMock()
    return attachment


@pytest.mark.parametrize(
    ("build_result", "expected"),
    [
        (
            lambda: GameStarted(
                game=_sample_game(),
                report=ActiveGameReport(
                    game_id=1,
                    game_name="Friday Night",
                    human_player_names=("Alice", "Bob"),
                    bot_count=0,
                ),
            ),
            _SUCCESS_REACTION,
        ),
        (
            lambda: GameEndNeedsConfirmation(game_id=1, interaction_id=10),
            _SUCCESS_REACTION,
        ),
        (
            lambda: GameEndNeedsPick(game_ids=(1, 2), interaction_id=11),
            _SUCCESS_REACTION,
        ),
        (lambda: GameEndPendingStart(interaction_id=12), _SUCCESS_REACTION),
        (
            lambda: WinRatioNeedsConfirmation(other_player_id=5, interaction_id=13),
            _SUCCESS_REACTION,
        ),
        (
            lambda: UnrecognizedScreenshot(
                message="Could not recognize this screenshot."
            ),
            _FAILURE_REACTION,
        ),
        (lambda: IngestError(message="Failed to read screenshot"), _FAILURE_REACTION),
    ],
)
def test_plan_ingest_ack_reaction_maps_result_to_final_emoji(
    build_result: object,
    expected: str,
) -> None:
    from scoretopia.discord.adapter import plan_ingest_ack_reaction

    assert plan_ingest_ack_reaction(build_result()) == expected


def test_begin_screenshot_ack_adds_processing_reaction() -> None:
    adapter = _screenshot_adapter(Path("/tmp"), MagicMock())
    message = _upload_message()

    asyncio.run(adapter._begin_screenshot_ack(message))

    message.add_reaction.assert_awaited_once_with(_PROCESSING_REACTION)


def test_finish_screenshot_ack_removes_processing_and_adds_success() -> None:
    adapter = _screenshot_adapter(Path("/tmp"), MagicMock())
    message = _upload_message()
    result = GameEndNeedsConfirmation(game_id=1, interaction_id=10)

    asyncio.run(adapter._begin_screenshot_ack(message))
    message.add_reaction.reset_mock()
    asyncio.run(adapter._finish_screenshot_ack(message, result))

    message.remove_reaction.assert_awaited_once_with(
        _PROCESSING_REACTION,
        adapter._bot.user,
    )
    message.add_reaction.assert_awaited_once_with(_SUCCESS_REACTION)


def test_finish_screenshot_ack_adds_failure_reaction_for_unrecognized() -> None:
    adapter = _screenshot_adapter(Path("/tmp"), MagicMock())
    message = _upload_message()
    result = UnrecognizedScreenshot(message="Could not recognize this screenshot.")

    asyncio.run(adapter._begin_screenshot_ack(message))
    message.add_reaction.reset_mock()
    asyncio.run(adapter._finish_screenshot_ack(message, result))

    message.remove_reaction.assert_awaited_once_with(
        _PROCESSING_REACTION,
        adapter._bot.user,
    )
    message.add_reaction.assert_awaited_once_with(_FAILURE_REACTION)


def test_screenshot_upload_reaction_lifecycle_for_staged_result(
    tmp_path: Path,
) -> None:
    stored_path = tmp_path / "start.png"
    result = _extraction_needs_confirmation()
    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.return_value = MagicMock()
    ingest_service.stage_screenshot.return_value = result

    adapter = _screenshot_adapter(tmp_path, ingest_service)
    adapter._deliver_ingest_result = AsyncMock()
    message = _upload_message()
    attachment = _upload_attachment("start.png")

    asyncio.run(adapter._handle_screenshot_upload(message, attachment))

    reaction_calls = [call.args[0] for call in message.add_reaction.await_args_list]
    assert reaction_calls[0] == _PROCESSING_REACTION
    assert _SUCCESS_REACTION in reaction_calls
    message.remove_reaction.assert_awaited_with(
        _PROCESSING_REACTION,
        adapter._bot.user,
    )
    adapter._deliver_ingest_result.assert_awaited_once_with(message, result)


def test_screenshot_upload_reaction_lifecycle_for_unrecognized_result(
    tmp_path: Path,
) -> None:
    stored_path = tmp_path / "bad.png"
    result = UnrecognizedScreenshot(message="Could not recognize this screenshot.")
    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.return_value = result

    adapter = _screenshot_adapter(tmp_path, ingest_service)
    adapter._deliver_ingest_result = AsyncMock()
    message = _upload_message()
    attachment = _upload_attachment("bad.png")

    asyncio.run(adapter._handle_screenshot_upload(message, attachment))

    reaction_calls = [call.args[0] for call in message.add_reaction.await_args_list]
    assert reaction_calls[0] == _PROCESSING_REACTION
    assert _FAILURE_REACTION in reaction_calls
    message.remove_reaction.assert_awaited_with(
        _PROCESSING_REACTION,
        adapter._bot.user,
    )
    adapter._deliver_ingest_result.assert_awaited_once_with(message, result)


def test_deliver_ingest_result_applies_final_ack_reaction(tmp_path: Path) -> None:
    adapter = _screenshot_adapter(tmp_path, MagicMock())
    message = _upload_message()
    result = UnrecognizedScreenshot(message="Could not recognize this screenshot.")

    asyncio.run(adapter._deliver_ingest_result(message, result))

    message.add_reaction.assert_awaited_with(_FAILURE_REACTION)


def test_multi_attachment_adds_processing_reaction_once() -> None:
    adapter = _screenshot_adapter(Path("/tmp"), MagicMock())
    message = _upload_message()

    asyncio.run(adapter._begin_screenshot_ack(message))
    asyncio.run(adapter._begin_screenshot_ack(message))

    processing_calls = [
        call.args[0]
        for call in message.add_reaction.await_args_list
        if call.args[0] == _PROCESSING_REACTION
    ]
    assert len(processing_calls) == 1


def test_multi_attachment_keeps_processing_reaction_until_all_complete() -> None:
    adapter = _screenshot_adapter(Path("/tmp"), MagicMock())
    message = _upload_message()
    success = GameEndNeedsConfirmation(game_id=1, interaction_id=10)

    asyncio.run(adapter._begin_screenshot_ack(message))
    asyncio.run(adapter._begin_screenshot_ack(message))
    asyncio.run(adapter._finish_screenshot_ack(message, success))
    message.remove_reaction.assert_not_awaited()

    asyncio.run(adapter._finish_screenshot_ack(message, success))
    message.remove_reaction.assert_awaited_once_with(
        _PROCESSING_REACTION,
        adapter._bot.user,
    )


def test_multi_attachment_does_not_duplicate_success_reaction() -> None:
    adapter = _screenshot_adapter(Path("/tmp"), MagicMock())
    message = _upload_message()
    existing = MagicMock()
    existing.emoji = _SUCCESS_REACTION
    message.reactions = [existing]
    success = GameEndNeedsConfirmation(game_id=1, interaction_id=10)

    asyncio.run(adapter._begin_screenshot_ack(message))
    asyncio.run(adapter._finish_screenshot_ack(message, success))
    asyncio.run(adapter._begin_screenshot_ack(message))
    asyncio.run(adapter._finish_screenshot_ack(message, success))

    success_calls = [
        call.args[0]
        for call in message.add_reaction.await_args_list
        if call.args[0] == _SUCCESS_REACTION
    ]
    assert len(success_calls) == 0


def test_reaction_api_failure_does_not_block_ingest_delivery(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stored_path = tmp_path / "shot.png"
    result = UnrecognizedScreenshot(message="Could not recognize this screenshot.")
    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.return_value = result

    adapter = _screenshot_adapter(tmp_path, ingest_service)
    adapter._deliver_ingest_result = AsyncMock()
    message = _upload_message()
    message.add_reaction = AsyncMock(side_effect=RuntimeError("missing permission"))
    attachment = _upload_attachment()

    with caplog.at_level(logging.WARNING):
        asyncio.run(adapter._handle_screenshot_upload(message, attachment))

    adapter._deliver_ingest_result.assert_awaited_once_with(message, result)
    assert any("missing permission" in record.message for record in caplog.records)


def test_screenshot_upload_applies_failure_reaction_on_unexpected_error(
    tmp_path: Path,
) -> None:
    stored_path = tmp_path / "shot.png"
    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.side_effect = RuntimeError("boom")

    adapter = _screenshot_adapter(tmp_path, ingest_service)
    adapter._deliver_ingest_result = AsyncMock()
    message = _upload_message()
    attachment = _upload_attachment()

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(adapter._handle_screenshot_upload(message, attachment))

    reaction_calls = [call.args[0] for call in message.add_reaction.await_args_list]
    assert reaction_calls[0] == _PROCESSING_REACTION
    assert _FAILURE_REACTION in reaction_calls
    message.remove_reaction.assert_awaited_with(
        _PROCESSING_REACTION,
        adapter._bot.user,
    )
    adapter._deliver_ingest_result.assert_not_awaited()


def test_readme_lists_add_reactions_permission() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )

    assert "Add Reactions" in readme


def _adapter_with_channels(
    *,
    reports_channel: MagicMock | None = None,
    input_channel: MagicMock | None = None,
) -> DiscordBotAdapter:
    reports_channel = reports_channel or MagicMock()
    reports_channel.send = AsyncMock()
    input_channel = input_channel or MagicMock()
    input_channel.send = AsyncMock()

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


def test_deliver_game_started_posts_unified_embed_to_reports_channel() -> None:
    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(reports_channel=reports_channel)
    message = _upload_message()
    result = GameStarted(
        game=_sample_game(),
        report=ActiveGameReport(
            game_id=1,
            game_name="Friday Night",
            human_player_names=("Alice", "Bob"),
            bot_count=1,
        ),
    )

    asyncio.run(adapter._deliver_ingest_result(message, result))

    reports_channel.send.assert_awaited_once()
    embed = reports_channel.send.await_args.kwargs["embed"]
    assert embed.title == "Game started: Friday Night"
    assert embed.colour.value == 0x57F287
    assert embed.timestamp is not None
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Players"] == "Alice, Bob"
    assert field_map["Bots"] == "1"


def test_post_game_completed_posts_unified_embed_with_winner() -> None:
    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(reports_channel=reports_channel)

    asyncio.run(
        adapter._post_game_completed(
            reports_channel,
            "Friday Night",
            winner_name="Alice",
        )
    )

    reports_channel.send.assert_awaited_once()
    embed = reports_channel.send.await_args.kwargs["embed"]
    assert embed.title == "Game completed: Friday Night"
    assert embed.colour.value == 0xFEE75C
    assert embed.timestamp is not None
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Winner"] == "Alice"


def test_reject_win_ratio_posts_dispute_embed_to_input_channel() -> None:
    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    adapter._other_player_discord_id_for_pending = MagicMock(return_value="999")
    adapter._win_ratio_service.reject.return_value = DisputeResult(
        dispute_id=1,
        message="Win-ratio dispute: Alice claimed 9–11 vs Bob.",
    )

    interaction = MagicMock()
    interaction.user.id = 999
    interaction.response.send_message = AsyncMock()
    parsed = MagicMock()
    parsed.interaction_id = 42

    asyncio.run(adapter._handle_reject_win_ratio(interaction, parsed))

    input_channel.send.assert_awaited_once()
    embed = input_channel.send.await_args.kwargs["embed"]
    assert embed.title == "Win-ratio dispute"
    assert embed.colour.value == 0xED4245
    assert embed.timestamp is not None
    assert "Alice claimed 9–11 vs Bob" in (embed.description or "")


def _extraction_needs_confirmation(
    *,
    interaction_id: int = 10,
    screenshot_type: str = "game_basics",
    game_name: str | None = "Friday Night",
) -> ExtractionNeedsConfirmation:
    return ExtractionNeedsConfirmation(
        interaction_id=interaction_id,
        preview=ExtractionPreview(
            screenshot_type=screenshot_type,
            game_name=game_name,
        ),
    )


def test_plan_ingest_response_routes_extraction_needs_confirmation_to_input() -> None:
    result = _extraction_needs_confirmation()

    plan = plan_ingest_response(result)

    assert plan.channel == "input"
    assert plan.kind == "extraction_confirm_view"


def test_deliver_extraction_preview_replies_on_input_not_reports() -> None:
    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(
        input_channel=input_channel,
        reports_channel=reports_channel,
    )
    message = _upload_message()
    result = _extraction_needs_confirmation()

    asyncio.run(adapter._deliver_ingest_result(message, result))

    message.reply.assert_awaited_once()
    reply_kwargs = message.reply.await_args.kwargs
    assert "embed" in reply_kwargs
    assert "view" in reply_kwargs
    assert reply_kwargs["embed"].title == "Game Basics"
    reports_channel.send.assert_not_awaited()


def test_handle_confirm_extraction_routes_to_continue_review() -> None:
    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(reports_channel=reports_channel)
    adapter._staged_uploader_id = MagicMock(return_value="42")
    committed = GameStarted(
        game=_sample_game(),
        report=ActiveGameReport(
            game_id=1,
            game_name="Friday Night",
            human_player_names=("Alice", "Bob"),
            bot_count=0,
        ),
    )
    adapter._ingest_service.continue_review.return_value = committed

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="confirm_extraction", interaction_id=10)

    asyncio.run(adapter._handle_confirm_extraction(interaction, parsed))

    adapter._ingest_service.continue_review.assert_called_once_with(
        10,
        confirmer_discord_id="42",
    )
    reports_channel.send.assert_awaited_once()


def test_handle_reject_extraction_opens_field_correction_without_reports_send() -> None:
    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(reports_channel=reports_channel)
    adapter._staged_uploader_id = MagicMock(return_value="42")
    from scoretopia.domain.actions import FieldCorrectionNeedsInput

    adapter._ingest_service.open_fix.return_value = FieldCorrectionNeedsInput(
        interaction_id=20,
        parent_extraction_interaction_id=10,
        screenshot_type="game_basics",
    )

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    parsed = ParsedCustomId(action="reject_extraction", interaction_id=10)

    asyncio.run(adapter._handle_reject_extraction(interaction, parsed))

    adapter._ingest_service.open_fix.assert_called_once_with(
        10,
        confirmer_discord_id="42",
    )
    reports_channel.send.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "view" in kwargs
    assert len(kwargs["view"].children) >= 1


def test_confirm_extraction_unauthorized_user_gets_ephemeral_message() -> None:
    adapter = _adapter_with_channels()
    adapter._staged_uploader_id = MagicMock(return_value="111")

    interaction = MagicMock()
    interaction.user.id = 222
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="confirm_extraction", interaction_id=10)

    asyncio.run(adapter._handle_confirm_extraction(interaction, parsed))

    interaction.response.send_message.assert_awaited_once_with(
        "not your confirmation",
        ephemeral=True,
    )
    adapter._ingest_service.continue_review.assert_not_called()


def test_game_basics_upload_preview_confirm_posts_to_reports_channel(
    tmp_path: Path,
) -> None:
    stored_path = tmp_path / "start.png"
    extraction = GameBasicsExtraction(
        game_name="Friday Night",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob"),
        ),
    )
    staged = _extraction_needs_confirmation(interaction_id=10)
    committed = GameStarted(
        game=_sample_game(),
        report=ActiveGameReport(
            game_id=1,
            game_name="Friday Night",
            human_player_names=("Alice", "Bob"),
            bot_count=0,
        ),
    )

    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.return_value = extraction
    ingest_service.stage_screenshot.return_value = staged
    ingest_service.continue_review.return_value = committed

    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _screenshot_adapter(tmp_path, ingest_service)
    adapter._channel_ids = {"input": 100, "reports": 200}
    adapter._channels_by_id = {200: reports_channel}
    adapter._staged_uploader_id = MagicMock(return_value="42")

    message = _upload_message(message_id=1001)
    attachment = _upload_attachment("start.png")

    asyncio.run(adapter._handle_screenshot_upload(message, attachment))

    ingest_service.stage_screenshot.assert_called_once()
    ingest_service.process_extracted_screenshot.assert_not_called()
    reports_channel.send.assert_not_awaited()
    message.reply.assert_awaited_once()
    assert "embed" in message.reply.await_args.kwargs
    assert "view" in message.reply.await_args.kwargs

    confirm_interaction = MagicMock()
    confirm_interaction.user.id = 42
    confirm_interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="confirm_extraction", interaction_id=10)

    asyncio.run(adapter._handle_confirm_extraction(confirm_interaction, parsed))

    reports_channel.send.assert_awaited_once()
    embed = reports_channel.send.await_args.kwargs["embed"]
    assert embed.title == "Game started: Friday Night"


# --- Player identity adapter wiring (Task 027) ---


def _player_link_needs_confirmation(
    *,
    interaction_id: int = 20,
    parent_id: int = 10,
    slot_index: int = 1,
    polytopia_name: str = "NewBob",
) -> object:
    from scoretopia.domain.actions import (
        PlayerLinkNeedsConfirmation,
        UnresolvedPlayerPreview,
    )

    return PlayerLinkNeedsConfirmation(
        interaction_id=interaction_id,
        parent_extraction_interaction_id=parent_id,
        unresolved=(
            UnresolvedPlayerPreview(
                slot_index=slot_index,
                polytopia_name=polytopia_name,
            ),
        ),
    )


def _player_link_adapter(
    *,
    identity_service: MagicMock | None = None,
    uploader_discord_id: str = "42",
    selected_discord_user_id: str | None = "999",
) -> DiscordBotAdapter:
    adapter = _adapter_with_channels()
    adapter._player_identity_service = identity_service or MagicMock()
    adapter._player_link_uploader_id = MagicMock(return_value=uploader_discord_id)
    adapter._player_link_selected_discord_id = MagicMock(
        return_value=selected_discord_user_id,
    )
    adapter._all_player_link_slots_resolved = MagicMock(return_value=False)
    return adapter


def _player_link_interaction(
    user_id: int,
    *,
    extra: dict[str, object] | None = None,
) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.data = extra
    interaction.channel = None
    return interaction


def test_plan_ingest_response_routes_player_link_needs_confirmation() -> None:
    result = _player_link_needs_confirmation()

    plan = plan_ingest_response(result)

    assert plan.channel == "input"
    assert plan.kind == "player_link_needs_confirmation"


def test_unregistered_uploader_gets_register_prompt_without_staging(
    tmp_path: Path,
) -> None:
    from scoretopia.storage.db import open_database
    from scoretopia.storage.repos import PendingInteractionRepo

    conn = open_database(":memory:")
    try:
        pending_repo = PendingInteractionRepo(conn)
        player_repo = MagicMock()
        player_repo.get_by_discord_id.return_value = None
        stored_path = tmp_path / "shot.png"
        extraction = GameBasicsExtraction(
            game_name="Friday Night",
            players=(GameBasicsPlayer(name="Alice", is_you=True),),
        )

        ingest_service = MagicMock()
        ingest_service.prepare_stored_path.return_value = stored_path
        ingest_service.extract_stored_screenshot.return_value = extraction

        adapter = _screenshot_adapter(tmp_path, ingest_service)
        adapter._player_repo = player_repo
        adapter._deliver_ingest_result = AsyncMock()

        message = _upload_message()
        message.author.id = 4242
        message.reply = AsyncMock()
        attachment = _upload_attachment()

        asyncio.run(adapter._handle_screenshot_upload(message, attachment))

        player_repo.get_by_discord_id.assert_called_once_with("4242")
        ingest_service.extract_stored_screenshot.assert_not_called()
        ingest_service.stage_screenshot.assert_not_called()
        message.reply.assert_awaited_once()
        reply_text = message.reply.await_args.args[0]
        assert "/register" in reply_text
        assert pending_repo.list_open_by_kind("confirm_extraction") == []
        adapter._deliver_ingest_result.assert_not_awaited()
    finally:
        conn.close()


def test_handle_confirm_extraction_delivers_player_link_spelling_ui() -> None:
    from scoretopia.discord.views import PlayerSpellingConfirmView

    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = _adapter_with_channels(reports_channel=reports_channel)
    adapter._staged_uploader_id = MagicMock(return_value="42")
    player_link = _player_link_needs_confirmation()
    adapter._ingest_service.continue_review.return_value = player_link

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="confirm_extraction", interaction_id=10)

    asyncio.run(adapter._handle_confirm_extraction(interaction, parsed))

    reports_channel.send.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs
    assert "view" in kwargs
    assert isinstance(kwargs["view"], PlayerSpellingConfirmView)
    assert kwargs["view"].player_slot == 1


@pytest.mark.parametrize(
    ("action", "identity_method", "interaction_user_id", "extra"),
    [
        (
            "confirm_player_spelling",
            "confirm_spelling",
            "42",
            {},
        ),
        (
            "reject_player_spelling",
            "reject_spelling",
            "42",
            {},
        ),
        (
            "pick_player_correction",
            "pick_canonical_player",
            "42",
            {"values": ["7"]},
        ),
        (
            "confirm_player_link",
            "confirm_remote_link",
            "999",
            {},
        ),
    ],
)
def test_player_link_component_calls_identity_service_method(
    action: str,
    identity_method: str,
    interaction_user_id: str,
    extra: dict[str, object],
) -> None:
    identity_service = MagicMock()
    adapter = _player_link_adapter(
        identity_service=identity_service,
        uploader_discord_id="42",
        selected_discord_user_id=(
            "999" if action == "confirm_player_link" else None
        ),
    )

    interaction = _player_link_interaction(
        int(interaction_user_id),
        extra=extra or None,
    )
    parsed = ParsedCustomId(
        action=action,
        interaction_id=20,
        player_slot=1,
    )

    asyncio.run(adapter._handle_component(interaction, parsed))

    method = getattr(identity_service, identity_method)
    method.assert_called_once()
    call_kwargs = method.call_args.kwargs
    assert call_kwargs["slot_index"] == 1
    if action == "pick_player_correction":
        assert call_kwargs["player_id"] == 7
        assert call_kwargs["picker_discord_id"] == "42"
    elif action == "confirm_player_link":
        assert call_kwargs["confirmer_discord_id"] == "999"
    else:
        assert call_kwargs["confirmer_discord_id"] == "42"


def test_reject_player_link_responds_without_confirming_remote_link() -> None:
    identity_service = MagicMock()
    adapter = _player_link_adapter(
        identity_service=identity_service,
        selected_discord_user_id="999",
    )

    interaction = _player_link_interaction(999)
    parsed = ParsedCustomId(
        action="reject_player_link",
        interaction_id=20,
        player_slot=1,
    )

    asyncio.run(adapter._handle_component(interaction, parsed))

    identity_service.confirm_remote_link.assert_not_called()
    interaction.response.send_message.assert_awaited_once()


def _build_identity_adapter_stack(
    tmp_path: Path,
) -> tuple[
    DiscordBotAdapter,
    object,
    object,
    object,
    object,
    MagicMock,
    object,
]:
    from scoretopia.domain.games import GameService
    from scoretopia.domain.ingest import IngestService
    from scoretopia.domain.player_identity import PlayerIdentityService
    from scoretopia.domain.players import PlayerService
    from scoretopia.domain.win_ratios import WinRatioService
    from scoretopia.storage.db import open_database
    from scoretopia.storage.repos import (
        DisputeRepo,
        GameParticipantRepo,
        GameRepo,
        PendingInteractionRepo,
        PlayerPairRatioRepo,
        PlayerRepo,
    )

    conn = open_database(":memory:")
    player_repo = PlayerRepo(conn)
    pending_repo = PendingInteractionRepo(conn)
    game_repo = GameRepo(conn)
    participant_repo = GameParticipantRepo(conn)
    ratio_repo = PlayerPairRatioRepo(conn)
    dispute_repo = DisputeRepo(conn)
    inbox_path = tmp_path / "inbox"
    inbox_path.mkdir()

    player_service = PlayerService(player_repo)
    game_service = GameService(game_repo, participant_repo, player_repo)
    win_ratio_service = WinRatioService(
        player_repo,
        pending_repo,
        ratio_repo,
        dispute_repo,
    )
    ingest_service = IngestService(
        player_service=player_service,
        game_service=game_service,
        win_ratio_service=win_ratio_service,
        pending_repo=pending_repo,
        inbox_path=inbox_path,
    )
    identity_service = PlayerIdentityService(player_repo, pending_repo)

    reports_channel = MagicMock()
    reports_channel.send = AsyncMock()
    adapter = DiscordBotAdapter(
        config=MagicMock(inbox=MagicMock(path=inbox_path)),
        ingest_service=ingest_service,
        game_service=game_service,
        win_ratio_service=win_ratio_service,
        player_service=player_service,
        report_service=MagicMock(),
        token="test-token",
        game_repo=game_repo,
        player_repo=player_repo,
    )
    adapter._channel_ids = {"input": 100, "reports": 200}
    adapter._channels_by_id = {200: reports_channel}
    adapter._player_identity_service = identity_service

    return (
        adapter,
        ingest_service,
        player_repo,
        pending_repo,
        identity_service,
        reports_channel,
        conn,
    )


def test_adapter_identity_flow_starts_game_after_remote_confirm(
    tmp_path: Path,
) -> None:
    (
        adapter,
        ingest_service,
        player_repo,
        pending_repo,
        identity_service,
        reports_channel,
        conn,
    ) = _build_identity_adapter_stack(tmp_path)
    try:
        player_repo.create(polytopia_name="Uploader", discord_user_id="100")
        extraction = GameBasicsExtraction(
            game_name="Adapter Identity Game",
            players=(
                GameBasicsPlayer(name="Uploader", is_you=True),
                GameBasicsPlayer(name="NewBob"),
            ),
        )

        ingest_service.prepare_stored_path = MagicMock(
            side_effect=lambda path: path,
        )
        with patch.object(
            ingest_service,
            "extract_stored_screenshot",
            return_value=extraction,
        ):
            message = _upload_message(message_id=2001)
            message.author.id = 100
            message.reply = AsyncMock()
            attachment = _upload_attachment("identity-start.png")

            asyncio.run(adapter._handle_screenshot_upload(message, attachment))

        staged = pending_repo.list_open_by_kind("confirm_extraction")
        assert len(staged) == 1
        parent_id = staged[0].id
        _fix_resolve_unresolved_roster_slots_for_tests(pending_repo, parent_id)

        confirm_extraction = MagicMock()
        confirm_extraction.user.id = 100
        confirm_extraction.response.send_message = AsyncMock()
        asyncio.run(
            adapter._handle_confirm_extraction(
                confirm_extraction,
                ParsedCustomId(action="confirm_extraction", interaction_id=parent_id),
            )
        )

        player_link_pending = pending_repo.list_open_by_kind("confirm_player_link")
        assert len(player_link_pending) == 1
        identity_id = player_link_pending[0].id

        confirm_spelling = MagicMock()
        confirm_spelling.user.id = 100
        confirm_spelling.response.send_message = AsyncMock()
        asyncio.run(
            adapter._handle_component(
                confirm_spelling,
                ParsedCustomId(
                    action="confirm_player_spelling",
                    interaction_id=identity_id,
                    player_slot=1,
                ),
            )
        )

        remote_confirm = MagicMock()
        remote_confirm.user.id = 200
        remote_confirm.response.send_message = AsyncMock()
        remote_confirm.response.is_done = MagicMock(return_value=False)
        remote_confirm.followup.send = AsyncMock()
        identity_service.select_discord_user(
            identity_id,
            slot_index=1,
            selected_discord_user_id="200",
            confirmer_discord_id="100",
        )
        asyncio.run(
            adapter._handle_component(
                remote_confirm,
                ParsedCustomId(
                    action="confirm_player_link",
                    interaction_id=identity_id,
                    player_slot=1,
                ),
            )
        )

        final_pending = pending_repo.list_open_by_kind("confirm_final_summary")
        assert len(final_pending) == 1
        confirm_final = MagicMock()
        confirm_final.user.id = 100
        confirm_final.response.send_message = AsyncMock()
        confirm_final.response.is_done = MagicMock(return_value=False)
        confirm_final.followup.send = AsyncMock()
        confirm_final.message = None
        asyncio.run(
            adapter._handle_confirm_final_summary(
                confirm_final,
                ParsedCustomId(
                    action="confirm_final_summary",
                    interaction_id=final_pending[0].id,
                ),
            )
        )

        reports_channel.send.assert_awaited_once()
        embed = reports_channel.send.await_args.kwargs["embed"]
        assert embed.title == "Game started: Adapter Identity Game"
        linked = player_repo.get_by_polytopia_name("NewBob")
        assert linked is not None
        assert linked.discord_user_id == "200"
    finally:
        conn.close()


def test_adapter_spelling_correction_flow_resumes_commit(
    tmp_path: Path,
) -> None:
    (
        adapter,
        ingest_service,
        player_repo,
        pending_repo,
        identity_service,
        reports_channel,
        conn,
    ) = _build_identity_adapter_stack(tmp_path)
    try:
        player_repo.create(polytopia_name="Uploader", discord_user_id="100")
        canonical = player_repo.create(polytopia_name="RealBob")
        extraction = GameBasicsExtraction(
            game_name="Correction Resume Game",
            players=(
                GameBasicsPlayer(name="Uploader", is_you=True),
                GameBasicsPlayer(name="WrngBob"),
            ),
        )

        ingest_service.prepare_stored_path = MagicMock(
            side_effect=lambda path: path,
        )
        with patch.object(
            ingest_service,
            "extract_stored_screenshot",
            return_value=extraction,
        ):
            message = _upload_message(message_id=3001)
            message.author.id = 100
            message.reply = AsyncMock()
            attachment = _upload_attachment("correction-start.png")

            asyncio.run(adapter._handle_screenshot_upload(message, attachment))

        parent_id = pending_repo.list_open_by_kind("confirm_extraction")[0].id
        _fix_resolve_unresolved_roster_slots_for_tests(pending_repo, parent_id)

        confirm_extraction = MagicMock()
        confirm_extraction.user.id = 100
        confirm_extraction.response.send_message = AsyncMock()
        asyncio.run(
            adapter._handle_confirm_extraction(
                confirm_extraction,
                ParsedCustomId(action="confirm_extraction", interaction_id=parent_id),
            )
        )

        identity_id = pending_repo.list_open_by_kind("confirm_player_link")[0].id

        reject_spelling = MagicMock()
        reject_spelling.user.id = 100
        reject_spelling.response.send_message = AsyncMock()
        asyncio.run(
            adapter._handle_component(
                reject_spelling,
                ParsedCustomId(
                    action="reject_player_spelling",
                    interaction_id=identity_id,
                    player_slot=1,
                ),
            )
        )

        pick_correction = MagicMock()
        pick_correction.user.id = 100
        pick_correction.response.send_message = AsyncMock()
        pick_correction.data = {"values": [str(canonical.id)]}
        asyncio.run(
            adapter._handle_component(
                pick_correction,
                ParsedCustomId(
                    action="pick_player_correction",
                    interaction_id=identity_id,
                    player_slot=1,
                ),
            )
        )

        remote_confirm = MagicMock()
        remote_confirm.user.id = 300
        remote_confirm.response.send_message = AsyncMock()
        remote_confirm.response.is_done = MagicMock(return_value=False)
        remote_confirm.followup.send = AsyncMock()
        identity_service.select_discord_user(
            identity_id,
            slot_index=1,
            selected_discord_user_id="300",
            confirmer_discord_id="100",
        )
        asyncio.run(
            adapter._handle_component(
                remote_confirm,
                ParsedCustomId(
                    action="confirm_player_link",
                    interaction_id=identity_id,
                    player_slot=1,
                ),
            )
        )

        final_pending = pending_repo.list_open_by_kind("confirm_final_summary")
        assert len(final_pending) == 1
        confirm_final = MagicMock()
        confirm_final.user.id = 100
        confirm_final.response.send_message = AsyncMock()
        confirm_final.response.is_done = MagicMock(return_value=False)
        confirm_final.followup.send = AsyncMock()
        confirm_final.message = None
        asyncio.run(
            adapter._handle_confirm_final_summary(
                confirm_final,
                ParsedCustomId(
                    action="confirm_final_summary",
                    interaction_id=final_pending[0].id,
                ),
            )
        )

        reports_channel.send.assert_awaited_once()
        embed = reports_channel.send.await_args.kwargs["embed"]
        assert embed.title == "Game started: Correction Resume Game"
        linked = player_repo.get_by_polytopia_name("RealBob")
        assert linked is not None
        assert linked.discord_user_id == "300"
    finally:
        conn.close()


# --- Bot mod approval (Task 029) ---


def _mod_approval_needs_confirmation(
    *,
    interaction_id: int = 30,
    parent_id: int = 10,
    summary: str = "WrngBob → RealBob",
) -> object:
    from scoretopia.domain.actions import ModApprovalNeedsConfirmation

    return ModApprovalNeedsConfirmation(
        interaction_id=interaction_id,
        parent_extraction_interaction_id=parent_id,
        summary=summary,
    )


def test_plan_ingest_response_routes_mod_approval_needs_confirmation() -> None:
    result = _mod_approval_needs_confirmation()

    plan = plan_ingest_response(result)  # type: ignore[arg-type]

    assert plan.channel == "input"
    assert plan.kind == "mod_approval_view"


def test_deliver_mod_approval_posts_embed_to_input_channel_with_mention() -> None:
    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    adapter._config.bot_mods.discord_user_ids = ("111111111111111111",)
    result = _mod_approval_needs_confirmation(summary="WrngBob → RealBob")
    message = _upload_message()

    asyncio.run(adapter._deliver_ingest_result(message, result))  # type: ignore[arg-type]

    input_channel.send.assert_awaited_once()
    call = input_channel.send.await_args
    kwargs = call.kwargs
    content = call.args[0] if call.args else kwargs.get("content", "")
    assert "<@111111111111111111>" in content
    assert "embed" in kwargs
    assert "view" in kwargs
    embed = kwargs["embed"]
    assert "WrngBob" in (embed.description or "") or any(
        "WrngBob" in field.value for field in embed.fields
    )


def test_mod_approval_unauthorized_user_cannot_approve() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    adapter._config.bot_mods.discord_user_ids = ("111111111111111111",)
    adapter._mod_approval_service = MagicMock()

    interaction = MagicMock()
    interaction.user.id = 222222222222222222
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="approve_mod_batch", interaction_id=30)

    asyncio.run(adapter._handle_approve_mod_batch(interaction, parsed))

    interaction.response.send_message.assert_awaited_once_with(
        "not your confirmation",
        ephemeral=True,
    )
    adapter._mod_approval_service.approve.assert_not_called()


# --- Final summary confirmation (Task 030) ---


def _final_summary_needs_confirmation(
    *,
    interaction_id: int = 40,
    parent_id: int = 10,
    game_name: str = "Final Summary Game",
) -> object:
    from scoretopia.domain.actions import (
        FinalSummaryNeedsConfirmation,
        FinalSummaryPreview,
    )

    return FinalSummaryNeedsConfirmation(
        interaction_id=interaction_id,
        parent_extraction_interaction_id=parent_id,
        summary=FinalSummaryPreview(
            screenshot_type="game_basics",
            game_name=game_name,
            map_size=400,
            terrain="Pangea",
            game_timer="24 hours",
            target_score=20000,
            game_type="Glory",
            roster=("Alice", "Bob"),
        ),
    )


def test_plan_ingest_response_routes_final_summary_needs_confirmation() -> None:
    result = _final_summary_needs_confirmation()

    plan = plan_ingest_response(result)  # type: ignore[arg-type]

    assert plan.channel == "input"
    assert plan.kind == "final_summary_view"


def test_deliver_final_summary_posts_embed_with_confirm_button() -> None:
    from scoretopia.discord.embeds import build_final_summary_embed
    from scoretopia.discord.views import FinalSummaryView, encode_custom_id

    input_channel = MagicMock()
    input_channel.send = AsyncMock()
    adapter = _adapter_with_channels(input_channel=input_channel)
    result = _final_summary_needs_confirmation(game_name="Doomed Gods")
    message = _upload_message()

    asyncio.run(adapter._deliver_ingest_result(message, result))  # type: ignore[arg-type]

    input_channel.send.assert_awaited_once()
    kwargs = input_channel.send.await_args.kwargs
    assert "embed" in kwargs
    assert "view" in kwargs
    embed = kwargs["embed"]
    view = kwargs["view"]
    assert isinstance(view, FinalSummaryView)
    assert "Doomed Gods" in (embed.title or "") or "Doomed Gods" in (
        embed.description or ""
    ) or any("Doomed Gods" in field.value for field in embed.fields)
    custom_ids = {child.custom_id for child in view.children}
    assert encode_custom_id("confirm_final_summary", interaction_id=40) in custom_ids
    assert encode_custom_id("fix_final_summary", interaction_id=40) in custom_ids
    assert encode_custom_id("abandon_final_summary", interaction_id=40) in custom_ids
    # Embed builder exists and produces a usable embed for the DTO.
    rebuilt = build_final_summary_embed(result.summary)  # type: ignore[attr-defined]
    assert rebuilt.title or rebuilt.description or rebuilt.fields


def test_final_summary_unauthorized_user_cannot_confirm() -> None:
    from scoretopia.discord.views import ParsedCustomId

    adapter = _adapter_with_channels()
    adapter._final_summary_uploader_id = MagicMock(return_value="111")
    adapter._ingest_service.confirm_final_summary = MagicMock()

    interaction = MagicMock()
    interaction.user.id = 222
    interaction.response.send_message = AsyncMock()
    parsed = ParsedCustomId(action="confirm_final_summary", interaction_id=40)

    asyncio.run(adapter._handle_confirm_final_summary(interaction, parsed))

    interaction.response.send_message.assert_awaited_once_with(
        "not your confirmation",
        ephemeral=True,
    )
    adapter._ingest_service.confirm_final_summary.assert_not_called()
