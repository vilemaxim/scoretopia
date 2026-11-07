"""Discord gateway bot adapter.

Create a bot at https://discord.com/developers, invite it with the ``bot`` and
``applications.commands`` scopes, and set the reports channel name in
``config/scoretopia.yaml``. Set ``SCORETOPIA_DISCORD_TOKEN`` in the environment
(never commit the token).

Games reach the bot as steam-test capture directories, polled from
``captures.path`` (:mod:`scoretopia.domain.captures`); the bot does not read
messages or screenshots posted in a channel.
"""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from scoretopia.config import ChannelsConfig, ScoretopiaConfig
from scoretopia.discord.publisher import DiscordReportPublisher, report_to_embed
from scoretopia.domain.captures import CaptureIngestService
from scoretopia.domain.players import PlayerService
from scoretopia.domain.results import RegisterOutcome
from scoretopia.ports.bot import BotPort
from scoretopia.reports.service import ReportService

logger = logging.getLogger(__name__)

_TOKEN_ENV_VAR = "SCORETOPIA_DISCORD_TOKEN"


class DiscordConfigError(ValueError):
    """Raised when Discord-specific configuration is missing or invalid."""


def load_discord_token() -> str:
    token = os.environ.get(_TOKEN_ENV_VAR)
    if not token:
        raise DiscordConfigError(
            f"Missing Discord bot token: set {_TOKEN_ENV_VAR} in the environment"
        )
    return token


def resolve_guild_channels(
    guild: discord.Guild | object,
    channels: ChannelsConfig,
) -> dict[str, int]:
    by_name = {channel.name: channel.id for channel in guild.text_channels}
    resolved: dict[str, int] = {}
    for key, channel_name in (("reports", channels.reports),):
        channel_id = by_name.get(channel_name)
        if channel_id is None:
            raise DiscordConfigError(f"Discord channel not found: {channel_name}")
        resolved[key] = channel_id
    return resolved


class DiscordBotAdapter(BotPort):
    """Gateway-mode Discord bot implementing :class:`BotPort`."""

    def __init__(
        self,
        *,
        config: ScoretopiaConfig,
        player_service: PlayerService,
        report_service: ReportService,
        token: str,
        capture_service: CaptureIngestService | None = None,
        capture_poll_seconds: int = 300,
    ) -> None:
        self._config = config
        self._capture_service = capture_service
        self._capture_poll_seconds = capture_poll_seconds
        self._capture_task: asyncio.Task[None] | None = None
        self._player_service = player_service
        self._report_service = report_service
        self._token = token
        intents = discord.Intents.default()
        intents.guilds = True
        self._bot = commands.Bot(command_prefix="!", intents=intents)
        self._channel_ids: dict[str, int] = {}
        self._channels_by_id: dict[int, discord.TextChannel] = {}
        self._publisher: DiscordReportPublisher | None = None
        self._register_handlers()

    def run(self) -> None:
        self._bot.run(self._token)

    def _register_handlers(self) -> None:
        @self._bot.event
        async def on_ready() -> None:
            guild = self._bot.guilds[0] if self._bot.guilds else None
            if guild is None:
                logger.warning("Bot is not in any guild; channel resolution skipped")
                return
            self._channel_ids = resolve_guild_channels(guild, self._config.channels)
            self._channels_by_id = {
                channel.id: channel
                for channel in guild.text_channels
                if channel.id in self._channel_ids.values()
            }
            self._publisher = DiscordReportPublisher(
                channel_lookup={
                    key: self._channels_by_id[channel_id]
                    for key, channel_id in self._channel_ids.items()
                },
                bot=self._bot,
            )
            await self._bot.tree.sync()
            logger.info("Discord bot ready; slash commands synced")
            self._start_capture_poll()

        @self._bot.tree.command(
            name="register",
            description="Link your Discord account to a Polytopia player name",
        )
        @app_commands.describe(polytopia_name="Your in-game Polytopia name")
        async def register(
            interaction: discord.Interaction,
            polytopia_name: str,
        ) -> None:
            assert interaction.user is not None
            result = self._player_service.register(
                discord_user_id=str(interaction.user.id),
                discord_display_name=interaction.user.display_name,
                polytopia_name=polytopia_name,
            )
            if result.outcome == RegisterOutcome.ALREADY_LINKED_TO_OTHER:
                await interaction.response.send_message(
                    "That Polytopia name is already linked to another Discord user.",
                    ephemeral=True,
                )
                return
            assert result.player is not None
            await interaction.response.send_message(
                f"Registered as **{result.player.polytopia_name}**.",
                ephemeral=True,
            )

        @self._bot.tree.command(
            name="report",
            description="Generate an on-demand report",
        )
        @app_commands.describe(
            name="Report name (active_games, recent_completions, win_ratios)"
        )
        async def report(interaction: discord.Interaction, name: str) -> None:
            generators = {
                "active_games": self._report_service.active_games,
                "recent_completions": lambda: self._report_service.recent_completions(
                    self._config.reports["recent_completions"].lookback_days or 14
                ),
                "win_ratios": self._report_service.win_ratios,
            }
            generator = generators.get(name)
            if generator is None:
                await interaction.response.send_message(
                    f"Unknown report: {name}",
                    ephemeral=True,
                )
                return
            dto = generator()
            reports_channel = self._channel("reports")
            if reports_channel is None:
                await interaction.response.send_message(
                    "Reports channel is not configured yet.",
                    ephemeral=True,
                )
                return
            await reports_channel.send(embed=report_to_embed(dto))
            await interaction.response.send_message(
                f"Posted **{dto.title}** to {reports_channel.mention}.",
                ephemeral=True,
            )

    def _channel(self, key: str) -> discord.TextChannel | None:
        channel_id = self._channel_ids.get(key)
        if channel_id is None:
            return None
        return self._channels_by_id.get(channel_id)

    def _start_capture_poll(self) -> None:
        """Start the capture poll once; ``on_ready`` fires again on reconnect."""
        if self._capture_service is None:
            return
        if self._capture_task is not None and not self._capture_task.done():
            return
        self._capture_task = asyncio.get_running_loop().create_task(
            self._capture_poll_loop()
        )

    async def _capture_poll_loop(self) -> None:
        while True:
            await self.poll_captures_once()
            await asyncio.sleep(self._capture_poll_seconds)

    async def poll_captures_once(self) -> int:
        """Process new capture directories and post each start and end.

        OCR runs in a worker thread so the gateway heartbeat keeps going; the
        capture service holds its own database connection for that thread.
        """
        if self._capture_service is None or self._publisher is None:
            return 0
        try:
            posts = await asyncio.to_thread(self._capture_service.collect)
        except Exception:
            logger.exception("Capture poll failed; retrying next poll")
            return 0
        for post in posts:
            self._publisher.publish(post.report_name, post.dto, post.channel_key)
        return len(posts)
