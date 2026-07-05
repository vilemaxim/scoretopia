"""Screenshot ingest orchestrator."""

from __future__ import annotations

import shutil
from pathlib import Path

from scoretopia.domain.actions import (
    ActiveGameReport,
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
from scoretopia.domain.players import PlayerService
from scoretopia.domain.results import MatchOutcome
from scoretopia.screenshot.extract import DEFAULT_MODEL_DIR, extract_screenshot
from scoretopia.screenshot.models import (
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameEndExtraction,
)
from scoretopia.storage.repos import PendingInteractionRepo

_UNRECOGNIZED_MESSAGE = (
    "Could not recognize this screenshot. Please upload a Polytopia "
    "game basics, game end, or friend profile screenshot."
)


class IngestService:
    def __init__(
        self,
        *,
        player_service: PlayerService,
        game_service: GameService,
        pending_repo: PendingInteractionRepo,
        inbox_path: Path,
        model_dir: str | Path = DEFAULT_MODEL_DIR,
    ) -> None:
        self._player_service = player_service
        self._game_service = game_service
        self._pending_repo = pending_repo
        self._inbox_path = inbox_path
        self._model_dir = model_dir
        self._inbox_path.mkdir(parents=True, exist_ok=True)

    def ingest(
        self,
        image_path: str | Path,
        *,
        uploader_discord_id: str,
    ) -> IngestResult:
        source = Path(image_path)
        stored_path = self._store_in_inbox(source)

        try:
            extraction = extract_screenshot(stored_path, model_dir=self._model_dir)
        except ValueError:
            return UnrecognizedScreenshot(message=_UNRECOGNIZED_MESSAGE)
        except FileNotFoundError as exc:
            return IngestError(message="Screenshot file not found", detail=str(exc))
        except OSError as exc:
            return IngestError(message="Failed to read screenshot", detail=str(exc))

        if isinstance(extraction, GameBasicsExtraction):
            return self._handle_game_basics(
                extraction,
                uploader_discord_id=uploader_discord_id,
            )
        if isinstance(extraction, GameEndExtraction):
            return self._handle_game_end(
                extraction,
                stored_path=stored_path,
                uploader_discord_id=uploader_discord_id,
            )
        if isinstance(extraction, FriendProfileExtraction):
            return self._handle_friend_profile(
                extraction,
                stored_path=stored_path,
                uploader_discord_id=uploader_discord_id,
            )
        return UnrecognizedScreenshot(message=_UNRECOGNIZED_MESSAGE)

    def _store_in_inbox(self, source: Path) -> Path:
        destination = self._inbox_path / source.name
        shutil.copy2(source, destination)
        return destination

    def _handle_game_basics(
        self,
        extraction: GameBasicsExtraction,
        *,
        uploader_discord_id: str,
    ) -> GameStarted:
        game_name = extraction.game_name or "Unnamed Game"
        game = self._game_service.start_game(
            name=game_name,
            extraction=extraction,
            uploader_id=uploader_discord_id,
        )
        player_names = tuple(player.name for player in extraction.players)
        report = ActiveGameReport(
            game_id=game.id,
            game_name=game.name,
            player_names=player_names,
        )
        return GameStarted(game=game, report=report)

    def _handle_game_end(
        self,
        extraction: GameEndExtraction,
        *,
        stored_path: Path,
        uploader_discord_id: str,
    ) -> GameEndNeedsConfirmation | GameEndNeedsPick | GameEndPendingStart:
        match = self._game_service.match_game_end(extraction)

        payload: dict[str, object] = {
            "screenshot_path": str(stored_path),
            "participant_names": [player.name for player in extraction.players],
            "winner": extraction.winner,
            "extraction": {
                "winner": extraction.winner,
                "players": [
                    {
                        "name": player.name,
                        "score": player.score,
                        "is_winner": player.is_winner,
                    }
                    for player in extraction.players
                ],
            },
        }

        if match.outcome == MatchOutcome.NONE:
            pending = self._create_pending(
                kind="game_end_pending_start",
                discord_user_id=uploader_discord_id,
                payload=payload,
            )
            return GameEndPendingStart(interaction_id=pending.id)

        if match.outcome == MatchOutcome.ONE:
            game = match.games[0]
            payload["game_id"] = game.id
            pending = self._create_pending(
                kind="confirm_game_end",
                discord_user_id=uploader_discord_id,
                payload=payload,
            )
            return GameEndNeedsConfirmation(
                game_id=game.id,
                interaction_id=pending.id,
            )

        game_ids = tuple(game.id for game in match.games)
        payload["game_ids"] = list(game_ids)
        pending = self._create_pending(
            kind="pick_game",
            discord_user_id=uploader_discord_id,
            payload=payload,
        )
        return GameEndNeedsPick(game_ids=game_ids, interaction_id=pending.id)

    def _create_pending(
        self,
        *,
        kind: str,
        discord_user_id: str,
        payload: dict[str, object],
    ):
        return self._pending_repo.create(
            kind=kind,
            discord_user_id=discord_user_id,
            payload=payload,
        )

    def _handle_friend_profile(
        self,
        extraction: FriendProfileExtraction,
        *,
        stored_path: Path,
        uploader_discord_id: str,
    ) -> WinRatioNeedsConfirmation:
        friend_name = extraction.friend_name or extraction.win_ratio.friend_name
        assert friend_name is not None
        friend = self._player_service.resolve_or_create_polytopia_name(friend_name)

        payload: dict[str, object] = {
            "screenshot_path": str(stored_path),
            "friend_name": friend_name,
            "you_wins": extraction.win_ratio.you_wins,
            "friend_wins": extraction.win_ratio.friend_wins,
            "other_player_id": friend.id,
        }
        pending = self._create_pending(
            kind="win_ratio_needs_confirmation",
            discord_user_id=uploader_discord_id,
            payload=payload,
        )
        return WinRatioNeedsConfirmation(
            other_player_id=friend.id,
            interaction_id=pending.id,
        )
