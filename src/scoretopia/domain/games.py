"""Game lifecycle domain service."""

from __future__ import annotations

from scoretopia.domain.players import PlayerService
from scoretopia.screenshot.models import GameBasicsExtraction
from scoretopia.storage.models import Game, GameParticipantInput
from scoretopia.storage.repos import GameParticipantRepo, GameRepo, PlayerRepo


def _is_bot_name(name: str) -> bool:
    return name.strip().lower().endswith(" bot")


class GameService:
    def __init__(
        self,
        game_repo: GameRepo,
        participant_repo: GameParticipantRepo,
        player_repo: PlayerRepo,
    ) -> None:
        self._game_repo = game_repo
        self._participant_repo = participant_repo
        self._player_service = PlayerService(player_repo)

    def start_game(
        self,
        *,
        name: str,
        extraction: GameBasicsExtraction,
    ) -> Game:
        for player in extraction.players:
            self._player_service.resolve_or_create_polytopia_name(player.name)
        return self._game_repo.create_active_game(
            name=name,
            map_size=extraction.map_size,
            terrain=extraction.terrain,
            game_type=extraction.game_type,
            target_score=extraction.target_score,
            game_timer=extraction.game_timer,
        )

    def add_participants_from_basics(
        self,
        game_id: int,
        extraction: GameBasicsExtraction,
    ) -> None:
        seen_player_ids: set[int] = set()
        participants: list[GameParticipantInput] = []
        for player in extraction.players:
            player_id = self._player_service.resolve_or_create_polytopia_name(
                player.name
            ).id
            if player_id in seen_player_ids:
                continue
            seen_player_ids.add(player_id)
            participants.append(
                GameParticipantInput(
                    player_id=player_id,
                    is_bot=_is_bot_name(player.name),
                )
            )
        self._participant_repo.add_participants(game_id, participants)

    def find_active_games_by_participants(
        self, participant_names: tuple[str, ...]
    ) -> list[Game]:
        return self._game_repo.list_active_by_participants(participant_names)
