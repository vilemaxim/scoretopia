"""Screenshot ingest orchestrator."""

from __future__ import annotations

import shutil
from dataclasses import asdict
from pathlib import Path

from scoretopia.domain.actions import (
    ActiveGameReport,
    ExtractionNeedsConfirmation,
    ExtractionPreview,
    GameEndNeedsConfirmation,
    GameEndNeedsPick,
    GameEndPendingStart,
    GameStarted,
    IngestError,
    IngestResult,
    PlayerLinkNeedsConfirmation,
    StagedIngestNotAuthorized,
    UnrecognizedScreenshot,
    WinRatioNeedsConfirmation,
)
from scoretopia.domain.games import GameService
from scoretopia.domain.matching import is_bot_name
from scoretopia.domain.player_identity import PlayerIdentityService
from scoretopia.domain.players import PlayerService
from scoretopia.domain.results import MatchOutcome, RejectResult
from scoretopia.domain.win_ratios import WinRatioService
from scoretopia.ingest import logger as ingest_logger
from scoretopia.screenshot.extract import DEFAULT_MODEL_DIR, extract_screenshot
from scoretopia.screenshot.models import (
    ExtractionResult,
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndHeader,
    GameEndPlayer,
    WinRatio,
)
from scoretopia.storage.repos import PendingInteractionRepo

_UNRECOGNIZED_MESSAGE = (
    "Could not recognize this screenshot. Please upload a Polytopia "
    "game basics, game end, or friend profile screenshot."
)
_CONFIRM_EXTRACTION_KIND = "confirm_extraction"


def _unrecognized_message_from_value_error(exc: ValueError) -> str:
    message = str(exc).strip()
    if not message or message.startswith("Unrecognized screenshot type"):
        return _UNRECOGNIZED_MESSAGE
    return message


def _human_and_bot_counts(
    players: tuple[GameBasicsPlayer, ...],
) -> tuple[tuple[str, ...], int]:
    human_names = tuple(
        player.name for player in players if not is_bot_name(player.name)
    )
    bot_count = sum(1 for player in players if is_bot_name(player.name))
    return human_names, bot_count


def _human_player_names(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in names if not is_bot_name(name))


class IngestService:
    def __init__(
        self,
        *,
        player_service: PlayerService,
        game_service: GameService,
        win_ratio_service: WinRatioService,
        pending_repo: PendingInteractionRepo,
        inbox_path: Path,
        model_dir: str | Path = DEFAULT_MODEL_DIR,
        player_identity_service: PlayerIdentityService | None = None,
    ) -> None:
        self._player_service = player_service
        self._game_service = game_service
        self._win_ratio_service = win_ratio_service
        self._pending_repo = pending_repo
        self._player_identity_service = (
            player_identity_service
            or PlayerIdentityService(
                player_service.player_repo,
                pending_repo,
            )
        )
        self._inbox_path = inbox_path
        self._model_dir = model_dir
        self._inbox_path.mkdir(parents=True, exist_ok=True)

    def ingest(
        self,
        image_path: str | Path,
        *,
        uploader_discord_id: str,
    ) -> IngestResult | StagedIngestNotAuthorized:
        source = Path(image_path)
        stored_path = self.prepare_stored_path(source)
        staged = self.stage_screenshot(
            stored_path,
            uploader_discord_id=uploader_discord_id,
            filename=source.name,
        )
        if isinstance(staged, (UnrecognizedScreenshot, IngestError)):
            return staged
        return self.commit_staged(
            staged.interaction_id,
            confirmer_discord_id=uploader_discord_id,
        )

    def stage_screenshot(
        self,
        stored_path: str | Path,
        *,
        uploader_discord_id: str,
        filename: str | None = None,
    ) -> ExtractionNeedsConfirmation | UnrecognizedScreenshot | IngestError:
        path = Path(stored_path)
        extracted = self.extract_stored_screenshot(path)
        if isinstance(extracted, (UnrecognizedScreenshot, IngestError)):
            self.report_extraction_failure(
                uploader_discord_id=uploader_discord_id,
                filename=filename or path.name,
                stored_path=path,
                failure=extracted,
            )
            return extracted

        self._log_screenshot_processed(
            uploader_discord_id=uploader_discord_id,
            filename=filename or path.name,
            stored_path=path,
            screenshot_type=extracted.screenshot_type,
        )
        self._log_extraction(extracted)

        payload: dict[str, object] = {
            "screenshot_path": str(path),
            "screenshot_type": extracted.screenshot_type,
            "uploader_discord_id": uploader_discord_id,
            "extraction": _serialize_extraction(extracted),
        }
        pending = self._create_pending(
            kind=_CONFIRM_EXTRACTION_KIND,
            discord_user_id=uploader_discord_id,
            payload=payload,
        )
        self._log_pending_interaction(kind=pending.kind, interaction_id=pending.id)
        preview = _extraction_preview(extracted)
        return ExtractionNeedsConfirmation(
            interaction_id=pending.id,
            preview=preview,
        )

    def commit_staged(
        self,
        interaction_id: int,
        *,
        confirmer_discord_id: str,
    ) -> IngestResult | StagedIngestNotAuthorized:
        pending = self._require_open_staged_pending(
            interaction_id,
            confirmer_discord_id=confirmer_discord_id,
        )
        if isinstance(pending, StagedIngestNotAuthorized):
            return pending

        stored_path = Path(str(pending.payload["screenshot_path"]))
        extraction = deserialize_staged_extraction(pending.payload)
        identity_result = self._resolve_player_identities(
            extraction,
            parent_interaction_id=interaction_id,
            uploader_discord_id=confirmer_discord_id,
        )
        if isinstance(identity_result, PlayerLinkNeedsConfirmation):
            return identity_result
        committed = self.complete_ingest(
            stored_path,
            extraction,
            uploader_discord_id=confirmer_discord_id,
        )
        self._pending_repo.resolve(interaction_id)
        return committed

    def reject_staged(
        self,
        interaction_id: int,
        *,
        confirmer_discord_id: str,
    ) -> RejectResult | StagedIngestNotAuthorized:
        pending = self._require_open_staged_pending(
            interaction_id,
            confirmer_discord_id=confirmer_discord_id,
        )
        if isinstance(pending, StagedIngestNotAuthorized):
            return pending

        self._pending_repo.resolve(interaction_id)
        return RejectResult(interaction_id=interaction_id)

    def _require_open_staged_pending(
        self,
        interaction_id: int,
        *,
        confirmer_discord_id: str,
    ):
        pending = self._pending_repo.get_by_id(interaction_id)
        if pending is None or pending.kind != _CONFIRM_EXTRACTION_KIND:
            return StagedIngestNotAuthorized()
        if pending.status != "open":
            return StagedIngestNotAuthorized()
        if pending.discord_user_id != confirmer_discord_id:
            return StagedIngestNotAuthorized()
        return pending

    def _resolve_player_identities(
        self,
        extraction: ExtractionResult,
        *,
        parent_interaction_id: int,
        uploader_discord_id: str,
    ) -> PlayerLinkNeedsConfirmation | None:
        unresolved = self._player_identity_service.list_unresolved_humans(extraction)
        if not unresolved:
            return None

        existing = self._player_identity_service.find_pending_for_parent(
            parent_interaction_id
        )
        if existing is not None:
            return existing

        return self._player_identity_service.begin_identity_check(
            parent_interaction_id=parent_interaction_id,
            uploader_discord_id=uploader_discord_id,
            extraction=extraction,
            unresolved=unresolved,
        )

    def prepare_stored_path(self, image_path: str | Path) -> Path:
        return self._store_in_inbox(Path(image_path))

    def extract_stored_screenshot(
        self,
        stored_path: str | Path,
    ) -> ExtractionResult | UnrecognizedScreenshot | IngestError:
        path = Path(stored_path)
        try:
            return extract_screenshot(path, model_dir=self._model_dir)
        except ValueError as exc:
            return UnrecognizedScreenshot(
                message=_unrecognized_message_from_value_error(exc)
            )
        except FileNotFoundError as exc:
            return IngestError(message="Screenshot file not found", detail=str(exc))
        except OSError as exc:
            return IngestError(message="Failed to read screenshot", detail=str(exc))

    def complete_ingest(
        self,
        stored_path: Path,
        extraction: ExtractionResult,
        *,
        uploader_discord_id: str,
    ) -> IngestResult:
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

    def process_extracted_screenshot(
        self,
        stored_path: Path,
        extraction: ExtractionResult,
        *,
        uploader_discord_id: str,
        filename: str,
    ) -> IngestResult:
        self._log_screenshot_processed(
            uploader_discord_id=uploader_discord_id,
            filename=filename,
            stored_path=stored_path,
            screenshot_type=extraction.screenshot_type,
        )
        self._log_extraction(extraction)
        return self.complete_ingest(
            stored_path,
            extraction,
            uploader_discord_id=uploader_discord_id,
        )

    def report_extraction_failure(
        self,
        *,
        uploader_discord_id: str,
        filename: str,
        stored_path: Path,
        failure: UnrecognizedScreenshot | IngestError,
    ) -> None:
        if isinstance(failure, UnrecognizedScreenshot):
            self._log_unrecognized_screenshot(
                uploader_discord_id=uploader_discord_id,
                filename=filename,
                stored_path=stored_path,
                reason=failure.message,
            )
            return
        self._log_ingest_error(
            uploader_discord_id=uploader_discord_id,
            filename=filename,
            stored_path=stored_path,
            message=failure.message,
        )

    def _store_in_inbox(self, source: Path) -> Path:
        destination = self._inbox_path / source.name
        if source.resolve() != destination.resolve():
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
        human_player_names, bot_count = _human_and_bot_counts(extraction.players)
        report = ActiveGameReport(
            game_id=game.id,
            game_name=game.name,
            human_player_names=human_player_names,
            bot_count=bot_count,
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
            human_names = _human_player_names(
                tuple(player.name for player in extraction.players)
            )
            active_rosters = self._game_service.active_game_roster_summaries()
            pending = self._create_pending(
                kind="game_end_pending_start",
                discord_user_id=uploader_discord_id,
                payload=payload,
            )
            self._log_pending_interaction(kind=pending.kind, interaction_id=pending.id)
            return GameEndPendingStart(
                interaction_id=pending.id,
                extracted_human_names=human_names,
                active_game_rosters=active_rosters,
            )

        if match.outcome == MatchOutcome.ONE:
            game = match.games[0]
            payload["game_id"] = game.id
            pending = self._create_pending(
                kind="confirm_game_end",
                discord_user_id=uploader_discord_id,
                payload=payload,
            )
            self._log_pending_interaction(kind=pending.kind, interaction_id=pending.id)
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
        self._log_pending_interaction(kind=pending.kind, interaction_id=pending.id)
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

    def _log_screenshot_processed(
        self,
        *,
        uploader_discord_id: str,
        filename: str,
        stored_path: Path,
        screenshot_type: str,
    ) -> None:
        ingest_logger.info(
            "screenshot processed uploader=%s filename=%s "
            "stored_path=%s screenshot_type=%s",
            uploader_discord_id,
            filename,
            stored_path,
            screenshot_type,
        )

    def _log_extraction(self, extraction: ExtractionResult) -> None:
        ingest_logger.debug("extraction payload %s", asdict(extraction))
        if isinstance(extraction, GameBasicsExtraction):
            human_names, bot_count = _human_and_bot_counts(extraction.players)
            ingest_logger.info(
                "extracted participants human_names=%s bot_count=%s",
                human_names,
                bot_count,
            )
        elif isinstance(extraction, GameEndExtraction):
            human_names = _human_player_names(
                tuple(player.name for player in extraction.players)
            )
            ingest_logger.info(
                "extracted participants human_names=%s",
                human_names,
            )
        elif isinstance(extraction, FriendProfileExtraction):
            ingest_logger.info(
                "extracted friend_profile friend_name=%s",
                extraction.friend_name,
            )

    def _log_unrecognized_screenshot(
        self,
        *,
        uploader_discord_id: str,
        filename: str,
        stored_path: Path,
        reason: str,
    ) -> None:
        ingest_logger.info(
            "unrecognized screenshot uploader=%s filename=%s stored_path=%s reason=%s",
            uploader_discord_id,
            filename,
            stored_path,
            reason,
        )

    def _log_ingest_error(
        self,
        *,
        uploader_discord_id: str,
        filename: str,
        stored_path: Path,
        message: str,
    ) -> None:
        ingest_logger.info(
            "ingest error uploader=%s filename=%s stored_path=%s message=%s",
            uploader_discord_id,
            filename,
            stored_path,
            message,
        )

    def _log_pending_interaction(self, *, kind: str, interaction_id: int) -> None:
        ingest_logger.info(
            "pending interaction created kind=%s interaction_id=%s",
            kind,
            interaction_id,
        )

    def _handle_friend_profile(
        self,
        extraction: FriendProfileExtraction,
        *,
        stored_path: Path,
        uploader_discord_id: str,
    ) -> WinRatioNeedsConfirmation:
        pending = self._win_ratio_service.submit_from_screenshot(
            extraction,
            uploader_discord_id,
            screenshot_path=str(stored_path),
        )
        return WinRatioNeedsConfirmation(
            other_player_id=pending.other_player_id,
            interaction_id=pending.interaction_id,
        )


def _extraction_preview(extraction: ExtractionResult) -> ExtractionPreview:
    if isinstance(extraction, GameBasicsExtraction):
        return ExtractionPreview(
            screenshot_type=extraction.screenshot_type,
            game_name=extraction.game_name,
        )
    return ExtractionPreview(screenshot_type=extraction.screenshot_type)


def _serialize_extraction(extraction: ExtractionResult) -> dict[str, object]:
    return asdict(extraction)


def deserialize_staged_extraction(payload: dict[str, object]) -> ExtractionResult:
    screenshot_type = payload.get("screenshot_type")
    extraction_data = payload.get("extraction")
    if not isinstance(extraction_data, dict):
        msg = "Missing extraction payload"
        raise ValueError(msg)
    if screenshot_type == "game_basics":
        return _game_basics_from_dict(extraction_data)
    if screenshot_type == "game_end":
        return _game_end_from_dict(extraction_data)
    if screenshot_type == "friend_profile":
        return _friend_profile_from_dict(extraction_data)
    msg = f"Unknown screenshot type: {screenshot_type!r}"
    raise ValueError(msg)


def _optional_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _optional_int(data: dict[str, object], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) else None


def _game_basics_from_dict(data: dict[str, object]) -> GameBasicsExtraction:
    players_data = data.get("players", [])
    players: list[GameBasicsPlayer] = []
    if isinstance(players_data, list):
        for entry in players_data:
            if isinstance(entry, dict):
                players.append(
                    GameBasicsPlayer(
                        name=str(entry["name"]),
                        is_you=bool(entry.get("is_you", False)),
                        is_eliminated=bool(entry.get("is_eliminated", False)),
                    )
                )
    return GameBasicsExtraction(
        screenshot_type="game_basics",
        game_name=_optional_str(data, "game_name"),
        map_size=_optional_int(data, "map_size"),
        terrain=_optional_str(data, "terrain"),
        target_score=_optional_int(data, "target_score"),
        game_type=_optional_str(data, "game_type"),
        game_timer=_optional_str(data, "game_timer"),
        win_condition_text=_optional_str(data, "win_condition_text"),
        turn_status=_optional_str(data, "turn_status"),
        players=tuple(players),
    )


def _game_end_from_dict(data: dict[str, object]) -> GameEndExtraction:
    header_data = data.get("header", {})
    header = GameEndHeader()
    if isinstance(header_data, dict):
        header = GameEndHeader(
            score=_optional_int(header_data, "score"),
            stars=_optional_int(header_data, "stars"),
            stars_gained=_optional_int(header_data, "stars_gained"),
            turn=_optional_int(header_data, "turn"),
        )
    players_data = data.get("players", [])
    players: list[GameEndPlayer] = []
    if isinstance(players_data, list):
        for entry in players_data:
            if isinstance(entry, dict):
                players.append(
                    GameEndPlayer(
                        name=str(entry["name"]),
                        tribe=_optional_str(entry, "tribe"),
                        status=_optional_str(entry, "status"),
                        score=_optional_int(entry, "score"),
                        elo_change=_optional_int(entry, "elo_change"),
                        elo=_optional_int(entry, "elo"),
                        is_winner=bool(entry.get("is_winner", False)),
                    )
                )
    winner = _optional_str(data, "winner")
    return GameEndExtraction(
        screenshot_type="game_end",
        winner=winner,
        header=header,
        players=tuple(players),
    )


def _friend_profile_from_dict(data: dict[str, object]) -> FriendProfileExtraction:
    ratio_data = data.get("win_ratio", {})
    win_ratio = WinRatio()
    if isinstance(ratio_data, dict):
        win_ratio = WinRatio(
            you_name=_optional_str(ratio_data, "you_name"),
            you_wins=_optional_int(ratio_data, "you_wins"),
            friend_name=_optional_str(ratio_data, "friend_name"),
            friend_wins=_optional_int(ratio_data, "friend_wins"),
        )
    return FriendProfileExtraction(
        screenshot_type="friend_profile",
        friend_name=_optional_str(data, "friend_name"),
        alias=_optional_str(data, "alias"),
        num_friends=_optional_int(data, "num_friends"),
        games_played=_optional_int(data, "games_played"),
        game_version=_optional_int(data, "game_version"),
        elo=_optional_int(data, "elo"),
        win_ratio=win_ratio,
    )
