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
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from scoretopia.config import ChannelsConfig, ScoretopiaConfig
from scoretopia.discord.embeds import (
    build_dispute_embed,
    build_extraction_preview_embed,
    build_final_summary_embed,
    build_game_completed_embed,
    build_game_started_embed,
    build_mod_approval_embed,
    build_player_remote_confirm_embed,
    build_player_spelling_confirm_embed,
)
from scoretopia.discord.publisher import DiscordReportPublisher, report_to_embed
from scoretopia.discord.views import (
    ExtractionConfirmView,
    FieldCorrectionView,
    FinalSummaryView,
    GameEndConfirmView,
    GameEndPickView,
    ModApprovalView,
    ParsedCustomId,
    PlayerCorrectionPickView,
    PlayerDiscordUserSelectView,
    PlayerLinkRemoteConfirmView,
    PlayerSpellingConfirmView,
    WinRatioConfirmView,
    can_approve_mod_batch,
    can_confirm_final_summary,
    can_confirm_game_end,
    can_confirm_player_link,
    can_confirm_win_ratio,
    can_review_staged,
    parse_custom_id,
    unauthorized_confirmation_message,
)
from scoretopia.domain.actions import (
    ExtractionNeedsConfirmation,
    FieldCorrectionNeedsInput,
    FinalSummaryNeedsConfirmation,
    GameEndNeedsConfirmation,
    GameEndNeedsPick,
    GameEndPendingStart,
    GameStarted,
    IngestError,
    IngestResult,
    ModApprovalNeedsConfirmation,
    PlayerLinkNeedsConfirmation,
    RosterSlotsUnresolved,
    StagedIngestNotAuthorized,
    UnrecognizedScreenshot,
    WinRatioNeedsConfirmation,
)
from scoretopia.domain.games import GameService
from scoretopia.domain.ingest import IngestService, deserialize_staged_extraction
from scoretopia.domain.mod_approval import ModApprovalService
from scoretopia.domain.player_identity import (
    ConfirmPlayerLinkOutcome,
    PlayerIdentityService,
)
from scoretopia.domain.players import PlayerService
from scoretopia.domain.results import RegisterOutcome
from scoretopia.domain.win_ratios import ConfirmOutcome, DisputeResult, WinRatioService
from scoretopia.ports.bot import BotPort
from scoretopia.reports.service import ReportService
from scoretopia.storage.models import Game
from scoretopia.storage.repos import GameRepo, PlayerRepo

_EXTRACT_FAILURE_TYPES = (UnrecognizedScreenshot, IngestError)

_PROCESSING_REACTION = "👀"
_SUCCESS_REACTION = "👍"
_FAILURE_REACTION = "❌"

ScreenshotUploadResult = IngestResult | ExtractionNeedsConfirmation

_FINAL_SUMMARY_PROMPT = (
    "Please Confirm the final game summary (commit gate). "
    "Use Fix to correct values, or Abandon to discard."
)
_ABANDON_STAGED_MESSAGE = (
    "Staged ingest abandoned — upload a new screenshot to start over."
)
_FIELD_CORRECTION_PROMPT = (
    "Fix the extracted values below. Continue from the diagnosis preview "
    "when ready; Confirm only appears on the final summary."
)
_UNREGISTERED_UPLOADER_MESSAGE = (
    "Please run `/register` first to link your Discord account to a "
    "Polytopia player name before uploading screenshots."
)
_PLAYER_LINK_KIND = "confirm_player_link"
_FINAL_SUMMARY_KIND = "confirm_final_summary"

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


def _payload_bool_map(
    payload: dict[str, object],
    key: str,
) -> dict[str, bool] | None:
    raw = payload.get(key)
    if not isinstance(raw, dict):
        return None
    return {str(index): bool(flag) for index, flag in raw.items()}


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


def plan_ingest_response(
    result: IngestResult | ExtractionNeedsConfirmation,
) -> ResponsePlan:
    if isinstance(result, GameStarted):
        return ResponsePlan(channel="reports", kind="embed")
    if isinstance(result, ExtractionNeedsConfirmation):
        return ResponsePlan(channel="input", kind="extraction_confirm_view")
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
    if isinstance(result, PlayerLinkNeedsConfirmation):
        return ResponsePlan(channel="input", kind="player_link_needs_confirmation")
    if isinstance(result, ModApprovalNeedsConfirmation):
        return ResponsePlan(channel="input", kind="mod_approval_view")
    if isinstance(result, FieldCorrectionNeedsInput):
        return ResponsePlan(channel="input", kind="field_correction_view")
    if isinstance(result, FinalSummaryNeedsConfirmation):
        return ResponsePlan(channel="input", kind="final_summary_view")
    if isinstance(result, (UnrecognizedScreenshot, IngestError)):
        return ResponsePlan(
            channel="input",
            kind="guidance_reply",
            body=result.message,
        )
    raise TypeError(f"Unsupported ingest result: {type(result)!r}")


def plan_ingest_ack_reaction(
    result: IngestResult | ExtractionNeedsConfirmation,
) -> Literal["👍", "❌"]:
    if isinstance(result, (UnrecognizedScreenshot, IngestError)):
        return _FAILURE_REACTION
    return _SUCCESS_REACTION


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
        self._player_identity_service: PlayerIdentityService = (
            ingest_service._player_identity_service
        )
        pending_repo = getattr(ingest_service, "_pending_repo", None)
        self._mod_approval_service = ModApprovalService(
            pending_repo,
            config=config,
            player_repo=self._player_repo,
        ) if pending_repo is not None else None
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        self._bot = commands.Bot(command_prefix="!", intents=intents)
        self._channel_ids: dict[str, int] = {}
        self._channels_by_id: dict[int, discord.TextChannel] = {}
        self._publisher: DiscordReportPublisher | None = None
        self._screenshot_ack_in_flight: dict[int, int] = {}
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
        await self._begin_screenshot_ack(message)
        uploader_discord_id = str(message.author.id)
        if (
            self._player_repo is not None
            and self._player_repo.get_by_discord_id(uploader_discord_id) is None
        ):
            await message.reply(_UNREGISTERED_UPLOADER_MESSAGE)
            await self._finish_screenshot_ack(
                message,
                IngestError(message="Uploader is not registered"),
            )
            return
        result: ScreenshotUploadResult | None = None
        try:
            result = await self._ingest_screenshot_attachment(message, attachment)
            await self._deliver_ingest_result(message, result)
        except Exception:
            await self._finish_screenshot_ack(
                message,
                IngestError(message="Screenshot processing failed"),
            )
            raise
        finally:
            if result is not None:
                await self._finish_screenshot_ack(message, result)

    async def _ingest_screenshot_attachment(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
    ) -> ScreenshotUploadResult:
        inbox_path = self._config.inbox.path
        inbox_path.mkdir(parents=True, exist_ok=True)
        destination = inbox_path / attachment.filename
        await attachment.save(destination)
        stored_path = self._ingest_service.prepare_stored_path(destination)
        extracted = await asyncio.to_thread(
            self._ingest_service.extract_stored_screenshot,
            stored_path,
        )
        uploader_discord_id = str(message.author.id)
        if isinstance(extracted, _EXTRACT_FAILURE_TYPES):
            self._ingest_service.report_extraction_failure(
                uploader_discord_id=uploader_discord_id,
                filename=attachment.filename,
                stored_path=stored_path,
                failure=extracted,
            )
            return extracted
        return self._ingest_service.stage_screenshot(
            stored_path,
            uploader_discord_id=uploader_discord_id,
            filename=attachment.filename,
        )

    async def _begin_screenshot_ack(self, message: discord.Message) -> None:
        message_id = message.id
        in_flight = self._screenshot_ack_in_flight.get(message_id, 0)
        self._screenshot_ack_in_flight[message_id] = in_flight + 1
        if in_flight == 0:
            await self._safe_add_reaction(message, _PROCESSING_REACTION)

    async def _finish_screenshot_ack(
        self,
        message: discord.Message,
        result: IngestResult | ExtractionNeedsConfirmation,
    ) -> None:
        message_id = message.id
        in_flight = self._screenshot_ack_in_flight.get(message_id, 0)
        if in_flight <= 0:
            await self._apply_final_ack_reaction(message, result)
            return
        in_flight -= 1
        self._screenshot_ack_in_flight[message_id] = in_flight
        if in_flight == 0:
            await self._safe_remove_reaction(message, _PROCESSING_REACTION)
            self._screenshot_ack_in_flight.pop(message_id, None)
        await self._apply_final_ack_reaction(message, result)

    async def _apply_final_ack_reaction(
        self,
        message: discord.Message,
        result: IngestResult | ExtractionNeedsConfirmation,
    ) -> None:
        emoji = plan_ingest_ack_reaction(result)
        if not self._message_has_reaction(message, emoji):
            await self._safe_add_reaction(message, emoji)

    async def _safe_add_reaction(
        self,
        message: discord.Message,
        emoji: str,
    ) -> None:
        try:
            await message.add_reaction(emoji)
        except Exception as exc:
            logger.warning("Failed to add reaction %s: %s", emoji, exc)

    async def _safe_remove_reaction(
        self,
        message: discord.Message,
        emoji: str,
    ) -> None:
        try:
            await message.remove_reaction(emoji, self._bot.user)
        except Exception as exc:
            logger.warning("Failed to remove reaction %s: %s", emoji, exc)

    def _message_has_reaction(self, message: discord.Message, emoji: str) -> bool:
        return any(str(reaction.emoji) == emoji for reaction in message.reactions)

    async def _deliver_ingest_result(
        self,
        message: discord.Message,
        result: IngestResult | ExtractionNeedsConfirmation,
    ) -> None:
        try:
            plan = plan_ingest_response(result)
            if plan.kind == "embed" and isinstance(result, GameStarted):
                await self._post_game_started_to_reports(result)
                return

            if plan.kind == "extraction_confirm_view" and isinstance(
                result, ExtractionNeedsConfirmation
            ):
                extraction = self._staged_extraction(result.interaction_id)
                (
                    resolved_roster,
                    slot_confirmations,
                    fix_resolved,
                ) = self._staged_roster_resolution(result.interaction_id)
                embed = build_extraction_preview_embed(
                    result.preview,
                    extraction=extraction,
                    resolved_roster=resolved_roster,
                )
                view = ExtractionConfirmView(
                    interaction_id=result.interaction_id,
                    uploader_discord_id=str(message.author.id),
                    resolved_roster=resolved_roster,
                    slot_confirmations=slot_confirmations,
                    fix_resolved_roster_slots=fix_resolved,
                )
                await message.reply(embed=embed, view=view)
                return

            if plan.kind == "field_correction_view" and isinstance(
                result, FieldCorrectionNeedsInput
            ):
                await self._post_field_correction(result)
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

            if plan.kind == "game_end_pick_view" and isinstance(
                result, GameEndNeedsPick
            ):
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
                    f"{other_mention}, please confirm or reject this win-ratio "
                    "screenshot."
                    if other_mention
                    else "Please confirm or reject this win-ratio screenshot."
                )
                await message.reply(content, view=view)
                return

            if plan.kind == "mod_approval_view" and isinstance(
                result, ModApprovalNeedsConfirmation
            ):
                await self._post_mod_approval(result)
                return

            if plan.kind == "final_summary_view" and isinstance(
                result, FinalSummaryNeedsConfirmation
            ):
                await self._post_final_summary(result)
                return

            if plan.body:
                await message.reply(plan.body)
        finally:
            await self._apply_final_ack_reaction(message, result)

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
        elif parsed.action == "continue_review":
            await self._handle_continue_review(interaction, parsed)
        elif parsed.action == "fix_extraction":
            await self._handle_fix_extraction(interaction, parsed)
        elif parsed.action == "abandon_staged":
            await self._handle_abandon_staged(interaction, parsed)
        elif parsed.action == "confirm_extraction":
            # Legacy custom_id — route to Continue (ADR 005).
            await self._handle_continue_review(interaction, parsed)
        elif parsed.action == "reject_extraction":
            # Legacy custom_id — route to Fix (ADR 005).
            await self._handle_fix_extraction(interaction, parsed)
        elif parsed.action == "confirm_final_summary":
            await self._handle_confirm_final_summary(interaction, parsed)
        elif parsed.action == "fix_final_summary":
            await self._handle_fix_final_summary(interaction, parsed)
        elif parsed.action == "abandon_final_summary":
            await self._handle_abandon_final_summary(interaction, parsed)
        elif parsed.action == "reject_final_summary":
            # Legacy custom_id — route to Fix from final summary.
            await self._handle_fix_final_summary(interaction, parsed)
        elif parsed.action == "confirm_player_spelling":
            await self._handle_confirm_player_spelling(interaction, parsed)
        elif parsed.action == "reject_player_spelling":
            await self._handle_reject_player_spelling(interaction, parsed)
        elif parsed.action == "pick_player_correction":
            await self._handle_pick_player_correction(interaction, parsed)
        elif parsed.action == "select_player_discord_user":
            await self._handle_select_player_discord_user(interaction, parsed)
        elif parsed.action == "confirm_player_link":
            await self._handle_confirm_player_link(interaction, parsed)
        elif parsed.action == "reject_player_link":
            await self._handle_reject_player_link(interaction, parsed)
        elif parsed.action == "approve_mod_batch":
            await self._handle_approve_mod_batch(interaction, parsed)
        elif parsed.action == "reject_mod_batch":
            await self._handle_reject_mod_batch(interaction, parsed)

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
        await self._post_game_completed(
            reports_channel,
            complete.game.name,
            winner_name=self._winner_name(complete.game),
        )
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
        await self._post_game_completed(
            reports_channel,
            complete.game.name,
            winner_name=self._winner_name(complete.game),
        )
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
            embed = build_dispute_embed(plan.body)
            await input_channel.send(embed=embed)
        await interaction.response.send_message(
            "Win ratio rejected and flagged as a dispute.",
            ephemeral=True,
        )

    async def _require_staged_uploader(
        self,
        interaction: discord.Interaction,
        interaction_id: int,
    ) -> str | None:
        pending = self._staged_uploader_id(interaction_id)
        if pending is None or not can_review_staged(
            uploader_discord_id=pending,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return None
        return pending

    async def _require_final_summary_actor(
        self,
        interaction: discord.Interaction,
        interaction_id: int,
    ) -> tuple[str, int] | None:
        uploader = self._final_summary_uploader_id(interaction_id)
        if uploader is None or not can_confirm_final_summary(
            uploader_discord_id=uploader,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return None
        parent_id = self._final_summary_parent_id(interaction_id)
        if parent_id is None:
            await self._reply_unauthorized(interaction)
            return None
        return uploader, parent_id

    async def _handle_continue_review(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        if (
            await self._require_staged_uploader(interaction, parsed.interaction_id)
            is None
        ):
            return
        result = self._ingest_service.continue_review(
            parsed.interaction_id,
            confirmer_discord_id=str(interaction.user.id),
        )
        if isinstance(result, StagedIngestNotAuthorized):
            await self._reply_unauthorized(interaction)
            return
        if isinstance(result, RosterSlotsUnresolved):
            await interaction.response.send_message(
                "Resolve fuzzy/new player slots with Fix before Continue.",
                ephemeral=True,
            )
            return
        if isinstance(result, PlayerLinkNeedsConfirmation):
            await self._deliver_player_link_spelling_ui(interaction, result)
            return
        if isinstance(result, FinalSummaryNeedsConfirmation):
            await self._deliver_final_summary_prompt(interaction, result)
            return
        await self._deliver_committed_ingest_result(interaction, result)

    async def _handle_confirm_extraction(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        """Legacy handler name — delegates to Continue (ADR 005)."""
        await self._handle_continue_review(interaction, parsed)

    async def _handle_fix_extraction(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        if (
            await self._require_staged_uploader(interaction, parsed.interaction_id)
            is None
        ):
            return
        fix_result = self._ingest_service.open_fix(
            parsed.interaction_id,
            confirmer_discord_id=str(interaction.user.id),
        )
        if isinstance(fix_result, StagedIngestNotAuthorized):
            await self._reply_unauthorized(interaction)
            return
        await self._deliver_field_correction_response(interaction, fix_result)

    async def _handle_reject_extraction(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        """Legacy handler name — delegates to Fix (ADR 005)."""
        await self._handle_fix_extraction(interaction, parsed)

    async def _handle_abandon_staged(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        if (
            await self._require_staged_uploader(interaction, parsed.interaction_id)
            is None
        ):
            return
        result = self._ingest_service.abandon_staged(
            parsed.interaction_id,
            confirmer_discord_id=str(interaction.user.id),
        )
        if isinstance(result, StagedIngestNotAuthorized):
            await self._reply_unauthorized(interaction)
            return
        await interaction.response.send_message(
            _ABANDON_STAGED_MESSAGE,
            ephemeral=True,
        )

    async def _deliver_final_summary_prompt(
        self,
        interaction: discord.Interaction,
        result: FinalSummaryNeedsConfirmation,
    ) -> None:
        await self._post_final_summary(result)
        if self._response_is_done(interaction):
            await interaction.followup.send(_FINAL_SUMMARY_PROMPT, ephemeral=True)
        else:
            await interaction.response.send_message(
                _FINAL_SUMMARY_PROMPT,
                ephemeral=True,
            )

    async def _handle_confirm_final_summary(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        uploader = self._final_summary_uploader_id(parsed.interaction_id)
        if uploader is None or not can_confirm_final_summary(
            uploader_discord_id=uploader,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        result = self._ingest_service.confirm_final_summary(
            parsed.interaction_id,
            confirmer_discord_id=str(interaction.user.id),
        )
        if isinstance(result, StagedIngestNotAuthorized):
            await self._reply_unauthorized(interaction)
            return
        await self._deliver_committed_ingest_result(interaction, result)

    async def _handle_fix_final_summary(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        actor = await self._require_final_summary_actor(
            interaction,
            parsed.interaction_id,
        )
        if actor is None:
            return
        uploader, parent_id = actor
        pending_repo = getattr(self._ingest_service, "_pending_repo", None)
        if pending_repo is not None:
            pending_repo.resolve(parsed.interaction_id)
        fix_result = self._ingest_service.open_fix(
            parent_id,
            confirmer_discord_id=uploader,
        )
        if isinstance(fix_result, StagedIngestNotAuthorized):
            await self._reply_unauthorized(interaction)
            return
        await self._deliver_field_correction_response(interaction, fix_result)

    async def _handle_reject_final_summary(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        """Legacy handler name — delegates to Fix from final summary."""
        await self._handle_fix_final_summary(interaction, parsed)

    async def _handle_abandon_final_summary(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        actor = await self._require_final_summary_actor(
            interaction,
            parsed.interaction_id,
        )
        if actor is None:
            return
        uploader, parent_id = actor
        result = self._ingest_service.abandon_staged(
            parent_id,
            confirmer_discord_id=uploader,
        )
        if isinstance(result, StagedIngestNotAuthorized):
            await self._reply_unauthorized(interaction)
            return
        await interaction.response.send_message(
            _ABANDON_STAGED_MESSAGE,
            ephemeral=True,
        )

    async def _post_field_correction(
        self,
        result: FieldCorrectionNeedsInput,
        *,
        uploader_discord_id: str | None = None,
    ) -> None:
        input_channel = self._channel("input")
        if input_channel is None:
            return
        uploader = uploader_discord_id or self._staged_uploader_id(
            result.parent_extraction_interaction_id
        ) or ""
        view = FieldCorrectionView(
            interaction_id=result.interaction_id,
            screenshot_type=result.screenshot_type,
            uploader_discord_id=uploader,
        )
        await input_channel.send(
            _FIELD_CORRECTION_PROMPT,
            view=view,
        )

    async def _deliver_field_correction_response(
        self,
        interaction: discord.Interaction,
        result: FieldCorrectionNeedsInput,
    ) -> None:
        view = FieldCorrectionView(
            interaction_id=result.interaction_id,
            screenshot_type=result.screenshot_type,
            uploader_discord_id=str(interaction.user.id),
        )
        if self._response_is_done(interaction):
            await interaction.followup.send(
                _FIELD_CORRECTION_PROMPT,
                view=view,
                ephemeral=False,
            )
            return
        await interaction.response.send_message(
            _FIELD_CORRECTION_PROMPT,
            view=view,
            ephemeral=False,
        )

    def _final_summary_uploader_id(self, interaction_id: int) -> str | None:
        pending_repo = getattr(self._ingest_service, "_pending_repo", None)
        if pending_repo is None:
            return None
        pending = pending_repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _FINAL_SUMMARY_KIND:
            return None
        return pending.discord_user_id

    def _final_summary_parent_id(self, interaction_id: int) -> int | None:
        pending_repo = getattr(self._ingest_service, "_pending_repo", None)
        if pending_repo is None:
            return None
        pending = pending_repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _FINAL_SUMMARY_KIND:
            return None
        parent_id = pending.payload.get("parent_extraction_interaction_id")
        return parent_id if isinstance(parent_id, int) else None

    async def _post_final_summary(
        self,
        result: FinalSummaryNeedsConfirmation,
    ) -> None:
        input_channel = self._channel("input")
        if input_channel is None:
            return
        embed = build_final_summary_embed(result.summary)
        view = FinalSummaryView(interaction_id=result.interaction_id)
        await input_channel.send(
            "Please confirm this game summary before commit.",
            embed=embed,
            view=view,
        )

    async def _deliver_committed_ingest_result(
        self,
        interaction: discord.Interaction,
        result: IngestResult,
    ) -> None:
        plan = plan_ingest_response(result)
        if plan.kind == "embed" and isinstance(result, GameStarted):
            await self._post_game_started_to_reports(result)
            message = f"Game started: **{result.report.game_name}**."
            if self._response_is_done(interaction):
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return

        message = interaction.message
        if message is not None:
            await self._deliver_ingest_result(message, result)
        confirmation = "Extraction confirmed."
        if self._response_is_done(interaction):
            await interaction.followup.send(confirmation, ephemeral=True)
        else:
            await interaction.response.send_message(confirmation, ephemeral=True)

    def _channel(self, key: str) -> discord.TextChannel | None:
        channel_id = self._channel_ids.get(key)
        if channel_id is None:
            return None
        return self._channels_by_id.get(channel_id)

    def _is_input_channel(self, channel: discord.abc.Messageable) -> bool:
        input_id = self._channel_ids.get("input")
        return isinstance(channel, discord.TextChannel) and channel.id == input_id

    async def _post_game_started_to_reports(self, result: GameStarted) -> None:
        reports_channel = self._channel("reports")
        if reports_channel is None:
            return
        embed = build_game_started_embed(result.game, result.report)
        await reports_channel.send(embed=embed)

    async def _post_game_completed(
        self,
        reports_channel: discord.TextChannel | None,
        game_name: str,
        *,
        winner_name: str | None = None,
    ) -> None:
        if reports_channel is None:
            return
        embed = build_game_completed_embed(game_name, winner_name=winner_name)
        await reports_channel.send(embed=embed)

    async def _post_mod_approval(
        self,
        result: ModApprovalNeedsConfirmation,
    ) -> None:
        input_channel = self._channel("input")
        if input_channel is None:
            return
        mod_ids = self._bot_mod_discord_ids()
        mention = f"<@{mod_ids[0]}>" if mod_ids else "bot mods"
        embed = build_mod_approval_embed(summary=result.summary)
        view = ModApprovalView(interaction_id=result.interaction_id)
        await input_channel.send(
            f"{mention}, please approve or reject this correction batch.",
            embed=embed,
            view=view,
        )

    def _bot_mod_discord_ids(self) -> tuple[str, ...]:
        raw = getattr(self._config.bot_mods, "discord_user_ids", ())
        if raw is None:
            return ()
        return tuple(str(entry) for entry in raw)

    async def _require_mod_batch_actor(
        self,
        interaction: discord.Interaction,
    ) -> str | None:
        actor_id = str(interaction.user.id)
        if not can_approve_mod_batch(
            bot_mod_discord_ids=self._bot_mod_discord_ids(),
            actor_discord_id=actor_id,
        ):
            await self._reply_unauthorized(interaction)
            return None
        if self._mod_approval_service is None:
            await self._reply_unauthorized(interaction)
            return None
        return actor_id

    async def _handle_approve_mod_batch(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        actor_id = await self._require_mod_batch_actor(interaction)
        if actor_id is None:
            return
        assert self._mod_approval_service is not None
        self._mod_approval_service.approve(
            parsed.interaction_id,
            approver_discord_id=actor_id,
        )
        await interaction.response.send_message(
            "Correction batch approved.",
            ephemeral=True,
        )

    async def _handle_reject_mod_batch(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        actor_id = await self._require_mod_batch_actor(interaction)
        if actor_id is None:
            return
        assert self._mod_approval_service is not None
        self._mod_approval_service.reject(
            parsed.interaction_id,
            rejector_discord_id=actor_id,
        )
        await interaction.response.send_message(
            "Correction batch rejected. Ask the uploader to revise or re-upload.",
            ephemeral=True,
        )

    def _winner_name(self, game: Game) -> str | None:
        if game.winner_player_id is None or self._player_repo is None:
            return None
        winner = self._player_repo.get_by_id(game.winner_player_id)
        return winner.polytopia_name if winner is not None else None

    async def _reply_unauthorized(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            unauthorized_confirmation_message(),
            ephemeral=True,
        )

    def _response_is_done(self, interaction: discord.Interaction) -> bool:
        is_done = getattr(interaction.response, "is_done", None)
        if callable(is_done):
            return is_done() is True
        return False

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

    def _staged_uploader_id(self, interaction_id: int) -> str | None:
        repo = getattr(self._ingest_service, "_pending_repo", None)
        if repo is None:
            return None
        pending = repo.get_by_id(interaction_id)
        if pending is None or pending.kind != "confirm_extraction":
            return None
        return pending.discord_user_id

    def _staged_extraction(self, interaction_id: int):
        repo = getattr(self._ingest_service, "_pending_repo", None)
        if repo is None:
            return None
        pending = repo.get_by_id(interaction_id)
        if pending is None:
            return None
        try:
            return deserialize_staged_extraction(pending.payload)
        except ValueError:
            return None

    def _staged_roster_resolution(
        self,
        interaction_id: int,
    ) -> tuple[
        list[dict[str, object]] | None,
        dict[str, bool] | None,
        dict[str, bool] | None,
    ]:
        repo = getattr(self._ingest_service, "_pending_repo", None)
        if repo is None:
            return None, None, None
        pending = repo.get_by_id(interaction_id)
        if pending is None:
            return None, None, None
        raw_roster = pending.payload.get("resolved_roster")
        resolved_roster: list[dict[str, object]] | None = None
        if isinstance(raw_roster, list):
            resolved_roster = [
                entry for entry in raw_roster if isinstance(entry, dict)
            ]
        return (
            resolved_roster,
            _payload_bool_map(pending.payload, "slot_confirmations"),
            _payload_bool_map(pending.payload, "fix_resolved_roster_slots"),
        )

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

    def _player_link_uploader_id(self, interaction_id: int) -> str | None:
        repo = getattr(self._ingest_service, "_pending_repo", None)
        if repo is None:
            return None
        pending = repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _PLAYER_LINK_KIND:
            return None
        return pending.discord_user_id

    def _player_link_slot(
        self,
        interaction_id: int,
        slot_index: int,
    ) -> dict[str, object] | None:
        repo = getattr(self._ingest_service, "_pending_repo", None)
        if repo is None:
            return None
        pending = repo.get_by_id(interaction_id)
        if pending is None:
            return None
        slots = pending.payload.get("slots")
        if not isinstance(slots, list):
            return None
        for slot in slots:
            if isinstance(slot, dict) and slot.get("slot_index") == slot_index:
                return slot
        return None

    def _player_link_selected_discord_id(
        self,
        interaction_id: int,
        slot_index: int,
    ) -> str | None:
        slot = self._player_link_slot(interaction_id, slot_index)
        if slot is None:
            return None
        selected = slot.get("selected_discord_user_id")
        return selected if isinstance(selected, str) else None

    def _all_player_link_slots_resolved(self, interaction_id: int) -> bool:
        repo = getattr(self._ingest_service, "_pending_repo", None)
        if repo is None:
            return False
        pending = repo.get_by_id(interaction_id)
        if pending is None:
            return False
        if pending.status != "open":
            return True
        slots = pending.payload.get("slots")
        if not isinstance(slots, list) or not slots:
            return False
        return all(
            bool(slot.get("resolved"))
            for slot in slots
            if isinstance(slot, dict)
        )

    def _player_link_parent_id(self, interaction_id: int) -> int | None:
        repo = getattr(self._ingest_service, "_pending_repo", None)
        if repo is None:
            return None
        pending = repo.get_by_id(interaction_id)
        if pending is None:
            return None
        parent_id = pending.payload.get("parent_extraction_interaction_id")
        return parent_id if isinstance(parent_id, int) else None

    async def _require_player_link_uploader(
        self,
        interaction: discord.Interaction,
        interaction_id: int,
    ) -> str | None:
        uploader = self._player_link_uploader_id(interaction_id)
        if uploader is None or not can_review_staged(
            uploader_discord_id=uploader,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return None
        return uploader

    async def _deliver_player_link_spelling_ui(
        self,
        interaction: discord.Interaction,
        result: PlayerLinkNeedsConfirmation,
    ) -> None:
        if not result.unresolved:
            await interaction.response.send_message(
                "No players need identity confirmation.",
                ephemeral=True,
            )
            return
        slot = result.unresolved[0]
        uploader = self._player_link_uploader_id(result.interaction_id) or ""
        embed = build_player_spelling_confirm_embed(slot.polytopia_name)
        view = PlayerSpellingConfirmView(
            interaction_id=result.interaction_id,
            player_slot=slot.slot_index,
            polytopia_name=slot.polytopia_name,
            uploader_discord_id=uploader,
        )
        await interaction.response.send_message(embed=embed, view=view)

    async def _handle_confirm_player_spelling(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        assert parsed.player_slot is not None
        uploader = await self._require_player_link_uploader(
            interaction,
            parsed.interaction_id,
        )
        if uploader is None:
            return
        self._player_identity_service.confirm_spelling(
            parsed.interaction_id,
            slot_index=parsed.player_slot,
            confirmer_discord_id=str(interaction.user.id),
        )
        await self._advance_player_link_slot(interaction, parsed)

    async def _handle_reject_player_spelling(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        assert parsed.player_slot is not None
        uploader = await self._require_player_link_uploader(
            interaction,
            parsed.interaction_id,
        )
        if uploader is None:
            return
        self._player_identity_service.reject_spelling(
            parsed.interaction_id,
            slot_index=parsed.player_slot,
            confirmer_discord_id=str(interaction.user.id),
        )
        players = self._player_repo.list_all() if self._player_repo is not None else []
        embed = build_player_spelling_confirm_embed("Pick the correct name")
        view = PlayerCorrectionPickView(
            interaction_id=parsed.interaction_id,
            player_slot=parsed.player_slot,
            players=players,
            uploader_discord_id=uploader,
        )
        await interaction.response.send_message(embed=embed, view=view)

    async def _handle_pick_player_correction(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        assert parsed.player_slot is not None
        uploader = await self._require_player_link_uploader(
            interaction,
            parsed.interaction_id,
        )
        if uploader is None:
            return
        values = interaction.data.get("values") if interaction.data else None
        if not values:
            await interaction.response.send_message(
                "No player selected.",
                ephemeral=True,
            )
            return
        self._player_identity_service.pick_canonical_player(
            parsed.interaction_id,
            slot_index=parsed.player_slot,
            player_id=int(values[0]),
            picker_discord_id=str(interaction.user.id),
        )
        await self._advance_player_link_slot(interaction, parsed)

    async def _handle_select_player_discord_user(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        assert parsed.player_slot is not None
        uploader = await self._require_player_link_uploader(
            interaction,
            parsed.interaction_id,
        )
        if uploader is None:
            return
        values = interaction.data.get("values") if interaction.data else None
        if not values:
            await interaction.response.send_message(
                "No Discord user selected.",
                ephemeral=True,
            )
            return
        selected_discord_id = str(values[0])
        self._player_identity_service.select_discord_user(
            parsed.interaction_id,
            slot_index=parsed.player_slot,
            selected_discord_user_id=selected_discord_id,
            confirmer_discord_id=str(interaction.user.id),
        )
        await self._send_player_remote_confirm(
            interaction,
            parsed.interaction_id,
            parsed.player_slot,
            selected_discord_id,
        )

    async def _handle_confirm_player_link(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        assert parsed.player_slot is not None
        selected = self._player_link_selected_discord_id(
            parsed.interaction_id,
            parsed.player_slot,
        )
        if selected is None or not can_confirm_player_link(
            selected_discord_user_id=selected,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        result = self._player_identity_service.confirm_remote_link(
            parsed.interaction_id,
            slot_index=parsed.player_slot,
            confirmer_discord_id=str(interaction.user.id),
        )
        if result.outcome == ConfirmPlayerLinkOutcome.NOT_AUTHORIZED:
            await self._reply_unauthorized(interaction)
            return
        if result.outcome == ConfirmPlayerLinkOutcome.BLOCKED:
            owner = result.blocked_owner_discord_id
            mention = f"<@{owner}>" if owner is not None else "another user"
            await interaction.response.send_message(
                f"That Polytopia name is already linked to {mention}.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Player link confirmed.",
            ephemeral=True,
        )
        await self._maybe_resume_parent_commit(interaction, parsed.interaction_id)

    async def _handle_reject_player_link(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        assert parsed.player_slot is not None
        selected = self._player_link_selected_discord_id(
            parsed.interaction_id,
            parsed.player_slot,
        )
        if selected is None or not can_confirm_player_link(
            selected_discord_user_id=selected,
            actor_discord_id=str(interaction.user.id),
        ):
            await self._reply_unauthorized(interaction)
            return
        slot = self._player_link_slot(parsed.interaction_id, parsed.player_slot)
        if slot is not None:
            slot["selected_discord_user_id"] = None
            repo = getattr(self._ingest_service, "_pending_repo", None)
            if repo is not None:
                pending = repo.get_by_id(parsed.interaction_id)
                if pending is not None:
                    repo.update_payload(parsed.interaction_id, pending.payload)
        uploader = self._player_link_uploader_id(parsed.interaction_id) or ""
        await interaction.response.send_message(
            "Link rejected. The uploader will pick another Discord user.",
            ephemeral=True,
        )
        if uploader:
            view = PlayerDiscordUserSelectView(
                interaction_id=parsed.interaction_id,
                player_slot=parsed.player_slot,
                uploader_discord_id=uploader,
            )
            channel = interaction.channel
            if channel is not None:
                await channel.send(
                    f"<@{uploader}>, please pick another Discord user for this player.",
                    view=view,
                )

    async def _advance_player_link_slot(
        self,
        interaction: discord.Interaction,
        parsed: ParsedCustomId,
    ) -> None:
        assert parsed.player_slot is not None
        selected = self._player_link_selected_discord_id(
            parsed.interaction_id,
            parsed.player_slot,
        )
        if selected is None:
            uploader = self._player_link_uploader_id(parsed.interaction_id) or ""
            view = PlayerDiscordUserSelectView(
                interaction_id=parsed.interaction_id,
                player_slot=parsed.player_slot,
                uploader_discord_id=uploader,
            )
            await interaction.response.send_message(
                "Who on Discord is this player?",
                view=view,
            )
            return
        await self._send_player_remote_confirm(
            interaction,
            parsed.interaction_id,
            parsed.player_slot,
            selected,
        )

    async def _send_player_remote_confirm(
        self,
        interaction: discord.Interaction,
        identity_interaction_id: int,
        slot_index: int,
        selected_discord_id: str,
    ) -> None:
        slot = self._player_link_slot(identity_interaction_id, slot_index)
        if slot is not None:
            polytopia_name = str(slot["polytopia_name"])
        else:
            polytopia_name = "this player"
        embed = build_player_remote_confirm_embed(polytopia_name)
        view = PlayerLinkRemoteConfirmView(
            interaction_id=identity_interaction_id,
            player_slot=slot_index,
            selected_discord_user_id=selected_discord_id,
        )
        content = (
            f"<@{selected_discord_id}>, please confirm or reject this player link."
        )
        if self._response_is_done(interaction):
            await interaction.followup.send(content, embed=embed, view=view)
        else:
            await interaction.response.send_message(content, embed=embed, view=view)

    async def _maybe_resume_parent_commit(
        self,
        interaction: discord.Interaction,
        identity_interaction_id: int,
    ) -> None:
        if not self._all_player_link_slots_resolved(identity_interaction_id):
            return
        parent_id = self._player_link_parent_id(identity_interaction_id)
        uploader = self._player_link_uploader_id(identity_interaction_id)
        if parent_id is None or uploader is None:
            return
        result = self._ingest_service.continue_review(
            parent_id,
            confirmer_discord_id=uploader,
        )
        if isinstance(result, PlayerLinkNeedsConfirmation):
            if self._response_is_done(interaction):
                await self._deliver_player_link_spelling_ui_followup(
                    interaction,
                    result,
                )
            else:
                await self._deliver_player_link_spelling_ui(interaction, result)
            return
        if isinstance(result, FinalSummaryNeedsConfirmation):
            await self._deliver_final_summary_prompt(interaction, result)
            return
        await self._deliver_committed_ingest_result(interaction, result)

    async def _deliver_player_link_spelling_ui_followup(
        self,
        interaction: discord.Interaction,
        result: PlayerLinkNeedsConfirmation,
    ) -> None:
        if not result.unresolved:
            return
        slot = result.unresolved[0]
        uploader = self._player_link_uploader_id(result.interaction_id) or ""
        embed = build_player_spelling_confirm_embed(slot.polytopia_name)
        view = PlayerSpellingConfirmView(
            interaction_id=result.interaction_id,
            player_slot=slot.slot_index,
            polytopia_name=slot.polytopia_name,
            uploader_discord_id=uploader,
        )
        await interaction.followup.send(embed=embed, view=view)
