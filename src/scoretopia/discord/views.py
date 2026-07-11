"""Discord UI views and interaction helpers.

Create a bot at https://discord.com/developers, invite it with the
``attachments`` and ``applications.commands`` scopes, and set channel names in
``config/scoretopia.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord

from scoretopia.domain.matching import is_bot_name
from scoretopia.storage.models import Game, Player

_CUSTOM_ID_PREFIX = "st"
_MAX_GAME_PICK_OPTIONS = 25
_MAX_PLAYER_PICK_OPTIONS = 25


@dataclass(frozen=True)
class ParsedCustomId:
    action: str
    interaction_id: int
    game_id: int | None = None
    player_slot: int | None = None


def unauthorized_confirmation_message() -> str:
    return "not your confirmation"


def can_confirm_game_end(*, uploader_discord_id: str, actor_discord_id: str) -> bool:
    return uploader_discord_id == actor_discord_id


def can_confirm_win_ratio(
    *, other_player_discord_id: str, actor_discord_id: str
) -> bool:
    return other_player_discord_id == actor_discord_id


def can_confirm_extraction(
    *, uploader_discord_id: str, actor_discord_id: str
) -> bool:
    return uploader_discord_id == actor_discord_id


def can_confirm_player_link(
    *, selected_discord_user_id: str, actor_discord_id: str
) -> bool:
    return selected_discord_user_id == actor_discord_id


_PLAYER_LINK_ACTIONS = frozenset(
    {
        "confirm_player_spelling",
        "reject_player_spelling",
        "pick_player_correction",
        "confirm_player_link",
        "reject_player_link",
        "select_player_discord_user",
    }
)


def encode_custom_id(
    action: str,
    *,
    interaction_id: int,
    game_id: int | None = None,
    player_slot: int | None = None,
) -> str:
    if game_id is not None:
        return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}:{game_id}"
    if player_slot is not None:
        return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}:{player_slot}"
    return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}"


def parse_custom_id(custom_id: str) -> ParsedCustomId:
    parts = custom_id.split(":")
    if len(parts) < 3 or parts[0] != _CUSTOM_ID_PREFIX:
        raise ValueError(f"Invalid custom_id: {custom_id}")
    action = parts[1]
    interaction_id = int(parts[2])
    qualifier = int(parts[3]) if len(parts) > 3 else None
    if action in _PLAYER_LINK_ACTIONS:
        return ParsedCustomId(
            action=action,
            interaction_id=interaction_id,
            player_slot=qualifier,
        )
    return ParsedCustomId(
        action=action,
        interaction_id=interaction_id,
        game_id=qualifier,
    )


def build_game_pick_options(games: list[Game]) -> list[discord.SelectOption]:
    limited = games[:_MAX_GAME_PICK_OPTIONS]
    return [
        discord.SelectOption(label=game.name, value=str(game.id))
        for game in limited
    ]


def build_player_pick_options(players: list[Player]) -> list[discord.SelectOption]:
    humans = [
        player
        for player in players
        if not is_bot_name(player.polytopia_name)
    ]
    limited = humans[:_MAX_PLAYER_PICK_OPTIONS]
    return [
        discord.SelectOption(label=player.polytopia_name, value=str(player.id))
        for player in limited
    ]


class GameEndConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        game_id: int,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.game_id = game_id
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_game_end",
                    interaction_id=interaction_id,
                    game_id=game_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Wrong game",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "reject_game_end",
                    interaction_id=interaction_id,
                ),
            )
        )


class GameEndPickView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        games: list[Game],
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        select = discord.ui.Select(
            placeholder="Which game ended?",
            options=build_game_pick_options(games),
            custom_id=encode_custom_id("pick_game_end", interaction_id=interaction_id),
        )
        self.add_item(select)


class WinRatioConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        other_player_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del other_player_discord_id
        self.interaction_id = interaction_id
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_win_ratio",
                    interaction_id=interaction_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Reject",
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "reject_win_ratio",
                    interaction_id=interaction_id,
                ),
            )
        )


class ExtractionConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_extraction",
                    interaction_id=interaction_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Reject",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "reject_extraction",
                    interaction_id=interaction_id,
                ),
            )
        )


class PlayerSpellingConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        polytopia_name: str,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del polytopia_name, uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        self.add_item(
            discord.ui.Button(
                label="Yes",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_player_spelling",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="No, pick different name",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "reject_player_spelling",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )


class PlayerCorrectionPickView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        players: list[Player],
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        select = discord.ui.Select(
            placeholder="Pick the correct Polytopia name",
            options=build_player_pick_options(players),
            custom_id=encode_custom_id(
                "pick_player_correction",
                interaction_id=interaction_id,
                player_slot=player_slot,
            ),
        )
        self.add_item(select)


class PlayerLinkRemoteConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        selected_discord_user_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del selected_discord_user_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_player_link",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Not me",
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "reject_player_link",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )


class PlayerDiscordUserSelectView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        select = discord.ui.UserSelect(
            placeholder="Which Discord user is this player?",
            custom_id=encode_custom_id(
                "select_player_discord_user",
                interaction_id=interaction_id,
                player_slot=player_slot,
            ),
            min_values=1,
            max_values=1,
        )
        self.add_item(select)
