"""Tests for Discord bot adapter helpers (Task 012)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scoretopia.config import ChannelsConfig, DiscordConfig
from scoretopia.discord.adapter import (
    DiscordBotAdapter,
    DiscordConfigError,
    load_discord_token,
    resolve_guild_channels,
    select_guild,
)
from scoretopia.ports.bot import BotPort


@dataclass(frozen=True)
class _FakeTextChannel:
    name: str
    id: int


@dataclass(frozen=True)
class _FakeGuild:
    id: int
    text_channels: tuple[_FakeTextChannel, ...] = ()


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


def test_select_guild_returns_the_configured_guild_whatever_the_order() -> None:
    guilds = [_FakeGuild(id=1), _FakeGuild(id=2), _FakeGuild(id=3)]

    assert select_guild(guilds, 2) is guilds[1]
    assert select_guild(guilds, 1) is guilds[0]
    assert select_guild(guilds[:2], 2) is guilds[1]
    assert select_guild([guilds[1]], 2) is guilds[1]


@pytest.mark.parametrize(
    "guilds",
    [[_FakeGuild(id=1)], [_FakeGuild(id=1), _FakeGuild(id=2)]],
)
def test_select_guild_fails_when_the_configured_guild_is_absent(
    guilds: list[_FakeGuild],
) -> None:
    with pytest.raises(DiscordConfigError) as excinfo:
        select_guild(guilds, 99)

    assert str(excinfo.value) == (
        "Configured Discord guild not found: the bot is not in discord.guild_id"
    )


def test_select_guild_uses_the_only_guild_when_unset() -> None:
    guild = _FakeGuild(id=1)

    assert select_guild([guild], None) is guild


def test_select_guild_refuses_to_guess_among_several_when_unset() -> None:
    guilds = [_FakeGuild(id=1), _FakeGuild(id=2), _FakeGuild(id=3)]

    with pytest.raises(DiscordConfigError) as excinfo:
        select_guild(guilds, None)

    assert str(excinfo.value) == "Bot is in 3 guilds; set discord.guild_id"


def test_select_guild_with_no_guilds_warns_and_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        assert select_guild([], None) is None
        assert select_guild([], 5) is None

    assert "Bot is not in any guild; channel resolution skipped" in caplog.text


def _ready_adapter(
    guilds: list[_FakeGuild], guild_id: int | None = None
) -> tuple[DiscordBotAdapter, MagicMock]:
    config = MagicMock()
    config.channels = ChannelsConfig(reports="polytopia-reports")
    config.discord = DiscordConfig(guild_id=guild_id)
    adapter = DiscordBotAdapter(
        config=config,
        player_service=MagicMock(),
        report_service=MagicMock(),
        token="test-token",
    )
    bot = MagicMock()
    bot.guilds = guilds
    bot.close = AsyncMock()
    bot.tree.sync = AsyncMock()
    adapter._bot = bot
    return adapter, bot


def _good_guild(guild_id: int = 7) -> _FakeGuild:
    return _FakeGuild(
        id=guild_id,
        text_channels=(_FakeTextChannel(name="polytopia-reports", id=200),),
    )


def _drive_run(adapter: DiscordBotAdapter, bot: MagicMock) -> None:
    """Make ``bot.run`` fire ``on_ready`` once, as discord.py does after login."""
    bot.run.side_effect = lambda token: asyncio.run(adapter._on_ready())
    adapter.run()


@pytest.mark.parametrize(
    ("guilds", "guild_id", "message"),
    [
        (
            [_good_guild(7)],
            99,
            "Configured Discord guild not found: the bot is not in discord.guild_id",
        ),
        (
            [_good_guild(7), _good_guild(8)],
            None,
            "Bot is in 2 guilds; set discord.guild_id",
        ),
        (
            [_FakeGuild(id=7, text_channels=(_FakeTextChannel("other", 1),))],
            None,
            "Discord channel not found: polytopia-reports",
        ),
        (
            [_FakeGuild(id=6), _good_guild(7)],
            6,
            "Discord channel not found: polytopia-reports",
        ),
    ],
)
def test_a_configuration_error_in_on_ready_closes_the_bot_and_fails_run(
    guilds: list[_FakeGuild],
    guild_id: int | None,
    message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, bot = _ready_adapter(guilds, guild_id)

    with caplog.at_level(logging.ERROR), pytest.raises(DiscordConfigError) as excinfo:
        _drive_run(adapter, bot)

    assert str(excinfo.value) == message
    bot.close.assert_awaited_once()
    assert message in caplog.text
    bot.tree.sync.assert_not_awaited()


def test_on_ready_uses_the_configured_guild_and_stays_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, bot = _ready_adapter([_FakeGuild(id=6), _good_guild(7)], guild_id=7)

    with caplog.at_level(logging.INFO):
        _drive_run(adapter, bot)

    bot.close.assert_not_awaited()
    bot.tree.sync.assert_awaited_once()
    assert adapter._channel_ids == {"reports": 200}
    assert "Discord bot ready; slash commands synced" in caplog.text


def test_on_ready_with_one_guild_and_no_guild_id_stays_open() -> None:
    adapter, bot = _ready_adapter([_good_guild(7)])

    _drive_run(adapter, bot)

    bot.close.assert_not_awaited()
    assert adapter._channel_ids == {"reports": 200}


def test_on_ready_in_no_guild_keeps_running_as_before(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, bot = _ready_adapter([])

    with caplog.at_level(logging.WARNING):
        _drive_run(adapter, bot)

    bot.close.assert_not_awaited()
    assert "Bot is not in any guild; channel resolution skipped" in caplog.text
