"""Discord gateway bot adapter.

Create a bot at https://discord.com/developers, invite it with the
``attachments`` and ``applications.commands`` scopes, and set channel names in
``config/scoretopia.yaml``. Set ``SCORETOPIA_DISCORD_TOKEN`` in the environment
(never commit the token).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from scoretopia.config import ChannelsConfig, ScoretopiaConfig
from scoretopia.discord.publisher import DiscordReportPublisher, report_to_embed
from scoretopia.discord.views import (
    GameEndConfirmView,
    GameEndPickView,
    ParsedCustomId,
    WinRatioConfirmView,
    can_confirm_game_end,
    can_confirm_win_ratio,
    parse_custom_id,
    unauthorized_confirmation_message,
)
from scoretopia.domain.actions import (
    GameEndNeedsConfirmation,
    GameEndNeedsPick,
    GameEndPendingStart,
    GameStarted,
    IngestError,
    IngestResult,
    UnrecognizedScreenshot,
    WinRatioNeedsConfirmation,
)
from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService
from scoretopia.domain.players import PlayerService
from scoretopia.domain.results import RegisterOutcome
from scoretopia.domain.win_ratios import ConfirmOutcome, DisputeResult, WinRatioService
from scoretopia.ports.bot import BotPort
from scoretopia.reports.service import ReportService
from scoretopia.storage.models import Game
from scoretopia.storage.repos import GameRepo, PlayerRepo

_EXTRACT_FAILURE_TYPES = (UnrecognizedScreenshot, IngestError)

logger = logging.getLogger(__name__)

_TOKEN_ENV_VAR = "SCORETOPIA_DISCORD_TOKEN"
_PENDING_START_MESSAGE = (
    "This looks like a game-end screenshot, but no matching active game was found. "
    "Upload a game-start (basics) screenshot first, then resend the game-end shot."
)


class DiscordConfigError(ValueError):
    """Raised when Discord-specific configuration is missing or invalid."""


@dataclass(frozen=True)
class ResponsePlan:
    channel: str
    kind: str
    body: str = ""


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
    for key, channel_name in (
        ("input", channels.input),
        ("reports", channels.reports),
    ):
        channel_id = by_name.get(channel_name)
        if channel_id is None:
            raise DiscordConfigError(f"Discord channel not found: {channel_name}")
        resolved[key] = channel_id
    return resolved


def plan_ingest_response(result: IngestResult) -> ResponsePlan:
    if isinstance(result, GameStarted):
        return ResponsePlan(channel="reports", kind="embed")
    if isinstance(result, GameEndNeedsConfirmation):
        return ResponsePlan(channel="input", kind="game_end_confirm_view")
    if isinstance(result, GameEndNeedsPick):
        return ResponsePlan(channel="input", kind="game_end_pick_view")
    if isinstance(result, GameEndPendingStart):
        return ResponsePlan(
            channel="input",
            kind="pending_start_reply",
            body=_PENDING_START_MESSAGE,
        )
    if isinstance(result, WinRatioNeedsConfirmation):
        return ResponsePlan(channel="input", kind="win_ratio_confirm_view")
    if isinstance(result, (UnrecognizedScreenshot, IngestError)):
        return ResponsePlan(
            channel="input",
            kind="guidance_reply",
            body=result.message,
        )
    raise TypeError(f"Unsupported ingest result: {type(result)!r}")


def plan_dispute_response(dispute: DisputeResult) -> ResponsePlan:
    return ResponsePlan(
        channel="input",
        kind="dispute_embed",
        body=dispute.message,
    )


class DiscordBotAdapter(BotPort):
    """Gateway-mode Discord bot implementing :class:`BotPort`."""

    def __init__(
        self,
        *,
        config: ScoretopiaConfig,
        ingest_service: IngestService,
        game_service: GameService,
        win_ratio_service: WinRatioService,
        player_service: PlayerService,
        report_service: ReportService,
        token: str,
        game_repo: GameRepo | None = None,
        player_repo: PlayerRepo | None = None,
    ) -> None:
        self._config = config
        self._ingest_service = ingest_service
        self._game_service = game_service
        self._win_ratio_service = win_ratio_service
        self._player_service = player_service
        self._report_service = report_service
        self._token = token
        self._game_repo = game_repo
        self._player_repo = player_repo or getattr(
            player_service, "_player_repo", None
        )
        intents = discord.Intents.default()
        intents.message_content = True
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

        @self._bot.event
        async def on_message(message: discord.Message) -> None:
            if message.author.bot:
                return
            if not self._is_input_channel(message.channel):
                return
            for attachment in message.attachments:
                content_type = attachment.content_type
                if not content_type or not content_type.startswith("image/"):
                    continue
                await self._handle_screenshot_upload(message, attachment)

        @self._bot.event
        async def on_interaction(interaction: discord.Interaction) -> None:
            if interaction.type != discord.InteractionType.component:
                return
            custom_id = interaction.data.get("custom_id") if interaction.data else None
            if not isinstance(custom_id, str) or not custom_id.startswith("st:"):
                return
            await self._handle_component(interaction, parse_custom_id(custom_id))

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

    async def _handle_screenshot_upload(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
    ) -> None:
        inbox_path = self._config.inbox.path
        inbox_path.mkdir(parents=True, exist_ok=True)
        destination = inbox_path / attachment.filename
        await attachment.save(destination)
        stored_path = self._ingest_service.prepare_stored_path(destination)
        extracted = await asyncio.to_thread(
            self._ingest_service.extract_stored_screenshot,
            stored_path,
        )
        if isinstance(extracted, _EXTRACT_FAILURE_TYPES):
            result: IngestResult = extracted
        else:
            result = self._ingest_service.complete_ingest(
                stored_path,
                extracted,
                uploader_discord_id=str(message.author.id),
            )
        await self._deliver_ingest_result(message, result)

    async def _deliver_ingest_result(
        self,
        message: discord.Message,
        result: IngestResult,
    ) -> None:
        plan = plan_ingest_response(result)
        if plan.kind == "embed" and isinstance(result, GameStarted):
            reports_channel = self._channel("reports")
            if reports_channel is not None:
                embed = discord.Embed(
                    title="Game started",
                    description=result.report.game_name,
                )
                embed.add_field(
                    name="Players",
                    value=", ".join(result.report.player_names),
                    inline=False,
                )
                await reports_channel.send(embed=embed)
            return

        if plan.kind == "game_end_confirm_view" and isinstance(
            result, GameEndNeedsConfirmation
        ):
            view = GameEndConfirmView(
                interaction_id=result.interaction_id,
                game_id=result.game_id,
                uploader_discord_id=str(message.author.id),
            )
            await message.reply(
                "Confirm this game end, or mark it as the wrong game.",
                view=view,
            )
            return

        if plan.kind == "game_end_pick_view" and isinstance(result, GameEndNeedsPick):
            games = self._games_for_ids(result.game_ids)
            view = GameEndPickView(
                interaction_id=result.interaction_id,
                games=games,
                uploader_discord_id=str(message.author.id),
            )
            await message.reply("Multiple active games match. Pick one:", view=view)
            return

        if plan.kind == "win_ratio_confirm_view" and isinstance(
            result, WinRatioNeedsConfirmation
        ):
            other_discord_id = self._other_player_discord_id(result.other_player_id)
            other_mention = (
                f"<@{other_discord_id}>" if other_discord_id is not None else ""
            )
            view = WinRatioConfirmView(
                interaction_id=result.interaction_id,
                other_player_discord_id=other_discord_id or "",
            )
            content = (
                f"{other_mention}, please confirm or reject this win-ratio screenshot."
                if other_mention
                else "Please confirm or reject this win-ratio screenshot."
            )
            await message.reply(content, view=view)
            return

        if plan.body:
            await message.reply(plan.body)

    async def _handle_component(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        if parsed.action == "confirm_game_end":
            await self._handle_confirm_game_end(interaction, parsed)
        elif parsed.action == "reject_game_end":
            await self._handle_reject_game_end(interaction, parsed)
        elif parsed.action == "pick_game_end":
            await self._handle_pick_game_end(interaction, parsed)
        elif parsed.action == "confirm_win_ratio":
            await self._handle_confirm_win_ratio(interaction, parsed)
        elif parsed.action == "reject_win_ratio":
            await self._handle_reject_win_ratio(interaction, parsed)

    async def _handle_confirm_game_end(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        pending = self._pending_uploader_id(parsed.interaction_id)
        if pending is None or not can_confirm_game_end(
            uploader_discord_id=pending,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        assert parsed.game_id is not None
        complete = self._game_service.confirm_game_end(
            interaction_id=parsed.interaction_id,
            game_id=parsed.game_id,
            confirmer_id=str(interaction.user.id),
        )
        reports_channel = self._channel("reports")
        await self._post_game_completed(reports_channel, complete.game.name)
        await interaction.response.send_message(
            f"Recorded completion for **{complete.game.name}**.",
            ephemeral=True,
        )

    async def _handle_reject_game_end(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        pending = self._pending_uploader_id(parsed.interaction_id)
        if pending is None or not can_confirm_game_end(
            uploader_discord_id=pending,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        self._game_service.reject_game_end(
            interaction_id=parsed.interaction_id,
            confirmer_id=str(interaction.user.id),
        )
        await interaction.response.send_message(
            "Marked as the wrong game. Upload the correct game-end screenshot.",
            ephemeral=True,
        )

    async def _handle_pick_game_end(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        pending = self._pending_uploader_id(parsed.interaction_id)
        if pending is None or not can_confirm_game_end(
            uploader_discord_id=pending,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        values = interaction.data.get("values") if interaction.data else None
        if not values:
            await interaction.response.send_message(
                "No game selected.",
                ephemeral=True,
            )
            return
        game_id = int(values[0])
        complete = self._game_service.confirm_game_end(
            interaction_id=parsed.interaction_id,
            game_id=game_id,
            confirmer_id=str(interaction.user.id),
        )
        reports_channel = self._channel("reports")
        await self._post_game_completed(reports_channel, complete.game.name)
        await interaction.response.send_message(
            f"Recorded completion for **{complete.game.name}**.",
            ephemeral=True,
        )

    async def _handle_confirm_win_ratio(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        other_discord_id = self._other_player_discord_id_for_pending(
            parsed.interaction_id
        )
        if other_discord_id is None or not can_confirm_win_ratio(
            other_player_discord_id=other_discord_id,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        result = self._win_ratio_service.confirm(
            parsed.interaction_id,
            str(interaction.user.id),
        )
        if result.outcome == ConfirmOutcome.NOT_AUTHORIZED:
            await self._reply_unauthorized(interaction)
            return
        await interaction.response.send_message(
            "Win ratio confirmed.",
            ephemeral=True,
        )

    async def _handle_reject_win_ratio(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        other_discord_id = self._other_player_discord_id_for_pending(
            parsed.interaction_id
        )
        if other_discord_id is None or not can_confirm_win_ratio(
            other_player_discord_id=other_discord_id,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        dispute = self._win_ratio_service.reject(
            parsed.interaction_id,
            str(interaction.user.id),
        )
        plan = plan_dispute_response(dispute)
        input_channel = self._channel("input")
        if input_channel is not None:
            embed = discord.Embed(
                title="Win-ratio dispute",
                description=plan.body,
            )
            await input_channel.send(embed=embed)
        await interaction.response.send_message(
            "Win ratio rejected and flagged as a dispute.",
            ephemeral=True,
        )

    def _channel(self, key: str) -> discord.TextChannel | None:
        channel_id = self._channel_ids.get(key)
        if channel_id is None:
            return None
        return self._channels_by_id.get(channel_id)

    def _is_input_channel(self, channel: discord.abc.Messageable) -> bool:
        input_id = self._channel_ids.get("input")
        return isinstance(channel, discord.TextChannel) and channel.id == input_id

    async def _post_game_completed(
        self,
        reports_channel: discord.TextChannel | None,
        game_name: str,
    ) -> None:
        if reports_channel is None:
            return
        embed = discord.Embed(title="Game completed", description=game_name)
        await reports_channel.send(embed=embed)

    async def _reply_unauthorized(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            unauthorized_confirmation_message(),
            ephemeral=True,
        )

    def _games_for_ids(self, game_ids: tuple[int, ...]) -> list[Game]:
        if self._game_repo is None:
            return [
                Game(
                    id=game_id,
                    name=f"Game {game_id}",
                    status="active",
                    map_size=None,
                    terrain=None,
                    game_type=None,
                    target_score=None,
                    game_timer=None,
                    winner_player_id=None,
                )
                for game_id in game_ids[:25]
            ]
        games: list[Game] = []
        for game_id in game_ids[:25]:
            game = self._game_repo.get_by_id(game_id)
            if game is not None:
                games.append(game)
        return games

    def _pending_uploader_id(self, interaction_id: int) -> str | None:
        repo = getattr(self._game_service, "_pending_repo", None)
        if repo is None:
            return None
        pending = repo.get_by_id(interaction_id)
        return pending.discord_user_id if pending is not None else None

    def _other_player_discord_id(self, other_player_id: int) -> str | None:
        if self._player_repo is None:
            return None
        other = self._player_repo.get_by_id(other_player_id)
        if other is None:
            return None
        return other.discord_user_id

    def _other_player_discord_id_for_pending(self, interaction_id: int) -> str | None:
        repo = getattr(self._win_ratio_service, "_pending_repo", None)
        if repo is None or self._player_repo is None:
            return None
        pending = repo.get_by_id(interaction_id)
        if pending is None:
            return None
        other_player_id = int(pending.payload["other_player_id"])
        return self._other_player_discord_id(other_player_id)
