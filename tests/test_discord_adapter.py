"""Tests for Discord bot adapter helpers (Task 012)."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
from scoretopia.domain.actions import (
    ActiveGameReport,
    GameEndNeedsConfirmation,
    GameEndNeedsPick,
    GameEndPendingStart,
    GameStarted,
    UnrecognizedScreenshot,
    WinRatioNeedsConfirmation,
)
from scoretopia.domain.win_ratios import DisputeResult
from scoretopia.ports.bot import BotPort
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
    ("result", "expected_channel", "expected_kind"),
    [
        (
            GameStarted(
                game=_sample_game(),
                report=ActiveGameReport(
                    game_id=1,
                    game_name="Friday Night",
                    player_names=("Alice", "Bob"),
                ),
            ),
            "reports",
            "embed",
        ),
        (
            GameEndNeedsConfirmation(game_id=1, interaction_id=10),
            "input",
            "game_end_confirm_view",
        ),
        (
            GameEndNeedsPick(game_ids=(1, 2), interaction_id=11),
            "input",
            "game_end_pick_view",
        ),
        (
            GameEndPendingStart(interaction_id=12),
            "input",
            "pending_start_reply",
        ),
        (
            WinRatioNeedsConfirmation(other_player_id=5, interaction_id=13),
            "input",
            "win_ratio_confirm_view",
        ),
        (
            UnrecognizedScreenshot(message="Could not recognize this screenshot."),
            "input",
            "guidance_reply",
        ),
    ],
)
def test_plan_ingest_response_routes_by_action_type(
    result: object,
    expected_channel: str,
    expected_kind: str,
) -> None:
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
    ingest_service.complete_ingest.assert_not_called()
    adapter._deliver_ingest_result.assert_awaited_once()


def test_screenshot_upload_completes_ingest_on_event_loop(tmp_path: Path) -> None:
    loop_thread_id = threading.get_ident()
    complete_thread_id: int | None = None
    stored_path = tmp_path / "end.png"
    extraction = MagicMock()
    expected = UnrecognizedScreenshot(message="Could not recognize this screenshot.")

    ingest_service = MagicMock()
    ingest_service.prepare_stored_path.return_value = stored_path
    ingest_service.extract_stored_screenshot.return_value = extraction

    def complete_ingest(
        _stored_path: Path,
        _extraction: object,
        *,
        uploader_discord_id: str,
    ) -> UnrecognizedScreenshot:
        nonlocal complete_thread_id
        complete_thread_id = threading.get_ident()
        assert uploader_discord_id == "99"
        return expected

    ingest_service.complete_ingest.side_effect = complete_ingest

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

    assert complete_thread_id == loop_thread_id
    ingest_service.complete_ingest.assert_called_once_with(
        stored_path,
        extraction,
        uploader_discord_id="99",
    )
    adapter._deliver_ingest_result.assert_awaited_once_with(message, expected)
