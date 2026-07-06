"""Discord UI views and interaction helpers.

Create a bot at https://discord.com/developers, invite it with the
``attachments`` and ``applications.commands`` scopes, and set channel names in
``config/scoretopia.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord

from scoretopia.storage.models import Game

_CUSTOM_ID_PREFIX = "st"
_MAX_GAME_PICK_OPTIONS = 25


@dataclass(frozen=True)
class ParsedCustomId:
    action: str
    interaction_id: int
    game_id: int | None = None


def unauthorized_confirmation_message() -> str:
    return "not your confirmation"


def can_confirm_game_end(*, uploader_discord_id: str, actor_discord_id: str) -> bool:
    return uploader_discord_id == actor_discord_id


def can_confirm_win_ratio(
    *, other_player_discord_id: str, actor_discord_id: str
) -> bool:
    return other_player_discord_id == actor_discord_id


def encode_custom_id(
    action: str,
    *,
    interaction_id: int,
    game_id: int | None = None,
) -> str:
    if game_id is not None:
        return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}:{game_id}"
    return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}"


def parse_custom_id(custom_id: str) -> ParsedCustomId:
    parts = custom_id.split(":")
    if len(parts) < 3 or parts[0] != _CUSTOM_ID_PREFIX:
        raise ValueError(f"Invalid custom_id: {custom_id}")
    action = parts[1]
    interaction_id = int(parts[2])
    game_id = int(parts[3]) if len(parts) > 3 else None
    return ParsedCustomId(
        action=action,
        interaction_id=interaction_id,
        game_id=game_id,
    )


def build_game_pick_options(games: list[Game]) -> list[discord.SelectOption]:
    limited = games[:_MAX_GAME_PICK_OPTIONS]
    return [
        discord.SelectOption(label=game.name, value=str(game.id))
        for game in limited
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
