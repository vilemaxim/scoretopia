"""Game lifecycle domain service."""

from __future__ import annotations

from scoretopia.domain.matching import (
    is_bot_name,
    normalize_participant_name,
    participant_sets_match,
)
from scoretopia.domain.players import PlayerService
from scoretopia.domain.results import (
    CompleteResult,
    MatchOutcome,
    MatchResult,
    RejectResult,
)
from scoretopia.ingest import logger as ingest_logger
from scoretopia.screenshot.models import GameBasicsExtraction, GameEndExtraction
from scoretopia.storage.models import Game, GameParticipantInput
from scoretopia.storage.repos import (
    GameParticipantRepo,
    GameRepo,
    PendingInteractionRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)


class GameService:
    def __init__(
        self,
        game_repo: GameRepo,
        participant_repo: GameParticipantRepo,
        player_repo: PlayerRepo,
        pending_repo: PendingInteractionRepo | None = None,
        ratio_repo: PlayerPairRatioRepo | None = None,
    ) -> None:
        self._game_repo = game_repo
        self._participant_repo = participant_repo
        self._player_service = PlayerService(player_repo)
        self._pending_repo = pending_repo
        self._ratio_repo = ratio_repo

    def start_game(
        self,
        *,
        extraction: GameBasicsExtraction,
        name: str | None = None,
        uploader_id: str | None = None,
    ) -> Game:
        if uploader_id is not None:
            self._player_service.auto_link_from_game_basics(
                uploader_discord_id=uploader_id,
                extraction=extraction,
            )
        for player in extraction.players:
            self._player_service.resolve_or_create_polytopia_name(player.name)
        game_name = name or extraction.game_name or "Unnamed Game"
        game = self._game_repo.create_active_game(
            name=game_name,
            map_size=extraction.map_size,
            terrain=extraction.terrain,
            game_type=extraction.game_type,
            target_score=extraction.target_score,
            game_timer=extraction.game_timer,
        )
        self.add_participants_from_basics(game.id, extraction)
        if self._pending_repo is not None and self._ratio_repo is not None:
            self.reconcile_pending_for_game(game.id)
        refreshed = self._game_repo.get_by_id(game.id)
        assert refreshed is not None
        return refreshed

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
                    is_bot=is_bot_name(player.name),
                )
            )
        self._participant_repo.add_participants(game_id, participants)

    def match_game_end(self, extraction: GameEndExtraction) -> MatchResult:
        end_names = tuple(player.name for player in extraction.players)
        human_names = tuple(
            player.name for player in extraction.players if not is_bot_name(player.name)
        )
        active_games = self._game_repo.list_active()
        active_game_count = len(active_games)
        matches: list[Game] = []
        for game in active_games:
            stored_names = self._participant_repo.get_participant_names(game.id)
            if participant_sets_match(stored_names, end_names):
                matches.append(game)

        if not matches:
            result = MatchResult(outcome=MatchOutcome.NONE)
        elif len(matches) == 1:
            result = MatchResult(outcome=MatchOutcome.ONE, games=(matches[0],))
        else:
            result = MatchResult(outcome=MatchOutcome.MANY, games=tuple(matches))
        self._log_game_end_match(
            active_game_count=active_game_count,
            human_names=human_names,
            result=result,
        )
        return result

    def confirm_game_end(
        self,
        *,
        interaction_id: int,
        game_id: int,
        confirmer_id: str,
    ) -> CompleteResult:
        del confirmer_id  # reserved for adapter authorization checks
        assert self._pending_repo is not None
        pending = self._pending_repo.get_by_id(interaction_id)
        assert pending is not None
        extraction_data = pending.payload.get("extraction")
        if not isinstance(extraction_data, dict):
            extraction_data = {"winner": pending.payload.get("winner"), "players": []}
        return self._complete_game_from_payload(game_id, extraction_data, pending.id)

    def reject_game_end(
        self,
        *,
        interaction_id: int,
        confirmer_id: str,
        note: str | None = None,
    ) -> RejectResult:
        del confirmer_id  # reserved for adapter authorization checks
        assert self._pending_repo is not None
        payload_updates: dict[str, object] = {}
        if note is not None:
            payload_updates["rejection_note"] = note
        self._pending_repo.resolve_with_payload(interaction_id, payload_updates)
        return RejectResult(interaction_id=interaction_id)

    def reconcile_pending_for_game(self, game_id: int) -> list[CompleteResult]:
        assert self._pending_repo is not None
        game_names = self._participant_repo.get_participant_names(game_id)
        results: list[CompleteResult] = []
        for pending in self._pending_repo.list_open_by_kind("game_end_pending_start"):
            participant_names = pending.payload.get("participant_names", [])
            if not isinstance(participant_names, list):
                continue
            if not participant_sets_match(game_names, participant_names):
                continue
            extraction_data = pending.payload.get("extraction")
            if not isinstance(extraction_data, dict):
                continue
            results.append(
                self._complete_game_from_payload(game_id, extraction_data, pending.id)
            )
        return results

    def _complete_game_from_payload(
        self,
        game_id: int,
        extraction_data: dict[str, object],
        interaction_id: int,
    ) -> CompleteResult:
        assert self._ratio_repo is not None
        assert self._pending_repo is not None

        winner_name = extraction_data.get("winner")
        assert isinstance(winner_name, str)
        winner = self._resolve_player_for_game(game_id, winner_name)
        game = self._game_repo.complete_game(game_id, winner.id)

        players_data = extraction_data.get("players", [])
        score_results: list[tuple[int, int, int]] = []
        human_player_ids: list[int] = []
        if isinstance(players_data, list):
            ranked: list[tuple[int, int]] = []
            for entry in players_data:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                score = entry.get("score")
                if not isinstance(name, str) or not isinstance(score, int):
                    continue
                player = self._resolve_player_for_game(game_id, name)
                if not is_bot_name(name):
                    human_player_ids.append(player.id)
                ranked.append((player.id, score))
            ranked.sort(key=lambda item: item[1], reverse=True)
            score_results = [
                (player_id, score, placement)
                for placement, (player_id, score) in enumerate(ranked, start=1)
            ]
        if score_results:
            self._participant_repo.update_participant_results(game_id, score_results)

        for other_id in human_player_ids:
            if other_id == winner.id:
                continue
            self._ratio_repo.increment_ratio(winner.id, other_id, source="computed")

        self._pending_repo.resolve(interaction_id)
        return CompleteResult(game=game)

    def _resolve_player_for_game(self, game_id: int, name: str):
        target = normalize_participant_name(name)
        for stored_name in self._participant_repo.get_participant_names(game_id):
            if normalize_participant_name(stored_name) == target:
                player = self._player_service.resolve_or_create_polytopia_name(
                    stored_name
                )
                return player
        return self._player_service.resolve_or_create_polytopia_name(name)

    def _log_game_end_match(
        self,
        *,
        active_game_count: int,
        human_names: tuple[str, ...],
        result: MatchResult,
    ) -> None:
        game_ids = tuple(game.id for game in result.games)
        ingest_logger.info(
            "game-end match active_game_count=%s human_names=%s outcome=%s game_ids=%s",
            active_game_count,
            human_names,
            result.outcome.name,
            game_ids,
        )
