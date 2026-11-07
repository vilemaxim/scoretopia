"""Tests for Discord bot adapter helpers (Task 012)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scoretopia.config import ChannelsConfig
from scoretopia.discord.adapter import (
    DiscordBotAdapter,
    DiscordConfigError,
    load_discord_token,
    resolve_guild_channels,
)
from scoretopia.ports.bot import BotPort


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
    channels = ChannelsConfig(reports="polytopia-reports")

    resolved = resolve_guild_channels(guild, channels)

    assert resolved == {"reports": 200}


def test_resolve_guild_channels_fails_fast_when_channel_missing() -> None:
    guild = MagicMock()
    guild.text_channels = [_FakeTextChannel(name="polytopia-screenshots", id=100)]
    channels = ChannelsConfig(reports="missing-channel")

    with pytest.raises(DiscordConfigError, match="missing-channel"):
        resolve_guild_channels(guild, channels)

def _bare_adapter(**kwargs: object) -> DiscordBotAdapter:
    return DiscordBotAdapter(
        config=MagicMock(),
        player_service=MagicMock(),
        report_service=MagicMock(),
        token="test-token",
        **kwargs,
    )


def test_adapter_registers_no_message_listener() -> None:
    adapter = _bare_adapter()

    assert "on_message" not in vars(adapter._bot)
    assert adapter._bot.intents.message_content is False


def test_poll_captures_once_publishes_each_post_to_reports() -> None:
    from scoretopia.domain.captures import CapturePost
    from scoretopia.reports.dto import ReportDTO

    started = ReportDTO(title="Game started: X", description="", fields=[])
    finished = ReportDTO(title="Game finished: X", description="", fields=[])
    capture_service = MagicMock()
    capture_service.collect.return_value = [
        CapturePost("game_started", started),
        CapturePost("game_finished", finished),
    ]
    adapter = _bare_adapter(capture_service=capture_service)
    adapter._publisher = MagicMock()

    assert asyncio.run(adapter.poll_captures_once()) == 2

    assert adapter._publisher.publish.call_args_list == [
        (("game_started", started, "reports"),),
        (("game_finished", finished, "reports"),),
    ]


def test_poll_captures_once_survives_a_failed_poll() -> None:
    capture_service = MagicMock()
    capture_service.collect.side_effect = RuntimeError("disk gone")
    adapter = _bare_adapter(capture_service=capture_service)
    adapter._publisher = MagicMock()

    assert asyncio.run(adapter.poll_captures_once()) == 0
    adapter._publisher.publish.assert_not_called()


def test_poll_captures_once_waits_for_the_publisher() -> None:
    capture_service = MagicMock()
    adapter = _bare_adapter(capture_service=capture_service)

    assert asyncio.run(adapter.poll_captures_once()) == 0
    capture_service.collect.assert_not_called()


def test_capture_poll_starts_once() -> None:
    capture_service = MagicMock()
    capture_service.collect.return_value = []
    adapter = _bare_adapter(capture_service=capture_service, capture_poll_seconds=3600)
    adapter._publisher = MagicMock()

    async def scenario() -> int:
        adapter._start_capture_poll()
        first = adapter._capture_task
        adapter._start_capture_poll()
        assert adapter._capture_task is first
        await asyncio.sleep(0.05)
        assert first is not None
        first.cancel()
        return capture_service.collect.call_count

    assert asyncio.run(scenario()) == 1


def test_capture_poll_is_off_without_a_capture_service() -> None:
    adapter = _bare_adapter()

    async def scenario() -> None:
        adapter._start_capture_poll()

    asyncio.run(scenario())
    assert adapter._capture_task is None

def test_discord_bot_adapter_implements_bot_port() -> None:
    assert isinstance(_bare_adapter(), BotPort)


def test_readme_lists_the_bot_permissions_it_needs() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )

    for permission in ("View Channels", "Send Messages", "Embed Links"):
        assert permission in readme
    assert "Message Content Intent** —" not in readme
