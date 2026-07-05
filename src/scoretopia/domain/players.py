"""Player identity registration and auto-linking."""

from __future__ import annotations

from scoretopia.domain.results import RegisterResult
from scoretopia.screenshot.models import GameBasicsExtraction
from scoretopia.storage.models import Player
from scoretopia.storage.repos import PlayerRepo


class PlayerService:
    def __init__(self, player_repo: PlayerRepo) -> None:
        self._player_repo = player_repo

    def register(
        self,
        *,
        discord_user_id: str,
        discord_display_name: str,
        polytopia_name: str,
    ) -> RegisterResult:
        by_name = self._player_repo.get_by_polytopia_name(polytopia_name)
        by_discord = self._player_repo.get_by_discord_id(discord_user_id)

        if self._polytopia_claimed_by_other(by_name, discord_user_id):
            return RegisterResult.already_linked_to_other()

        if by_discord is not None:
            if by_name is not None and by_name.id != by_discord.id:
                return RegisterResult.already_linked_to_other()
            player = self._link_discord(
                by_discord.id,
                discord_user_id=discord_user_id,
                discord_display_name=discord_display_name,
            )
            return RegisterResult.success(player)

        if by_name is not None:
            player = self._link_discord(
                by_name.id,
                discord_user_id=discord_user_id,
                discord_display_name=discord_display_name,
            )
            return RegisterResult.success(player)

        player = self._player_repo.create(
            polytopia_name=polytopia_name,
            discord_user_id=discord_user_id,
            discord_display_name=discord_display_name,
        )
        return RegisterResult.success(player)

    def auto_link_from_game_basics(
        self,
        *,
        uploader_discord_id: str,
        extraction: GameBasicsExtraction,
    ) -> Player | None:
        you = next((player for player in extraction.players if player.is_you), None)
        if you is None:
            return None

        player = self.resolve_or_create_polytopia_name(you.name)

        if player.discord_user_id == uploader_discord_id:
            return player

        if self._polytopia_claimed_by_other(player, uploader_discord_id):
            return None

        return self._link_discord(
            player.id,
            discord_user_id=uploader_discord_id,
            discord_display_name=None,
        )

    def resolve_or_create_polytopia_name(self, name: str) -> Player:
        existing = self._player_repo.get_by_polytopia_name(name)
        if existing is not None:
            return existing
        return self._player_repo.create(polytopia_name=name)

    def _polytopia_claimed_by_other(
        self,
        player: Player | None,
        discord_user_id: str,
    ) -> bool:
        return (
            player is not None
            and player.discord_user_id is not None
            and player.discord_user_id != discord_user_id
        )

    def _link_discord(
        self,
        player_id: int,
        *,
        discord_user_id: str,
        discord_display_name: str | None,
    ) -> Player:
        return self._player_repo.update_discord_link(
            player_id,
            discord_user_id=discord_user_id,
            discord_display_name=discord_display_name,
        )
