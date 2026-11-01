"""Ingest steam-test capture directories and announce game starts and ends.

steam-test's capture playbook (homelab-playbooks
``playbooks/steam-polytopia-capture.yml``) writes one directory per game per
kind under the capture root::

    <root>/setup/<key>/   card.png, info.png, manifest.json
    <root>/result/<key>/  setup.png, info.png, victory.png, manifest.json

``manifest.json`` holds ``kind``, ``captured_at``, ``screens`` (screen name →
file name) and ``start`` (``unknown`` until a screen can tell a game the bot
created from one it was invited to). The playbook writes the manifest after the
screens, so a directory without one is still being written and is left for a
later poll.

Each directory is processed once and recorded in ``capture_manifests``. A
failed extraction records nothing and posts nothing, so the next poll retries
it. A game id read from ``info.png`` de-duplicates a game captured twice; with
no id, the directory is the unit.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scoretopia.domain.games import GameService
from scoretopia.domain.matching import normalize_participant_name
from scoretopia.domain.results import MatchOutcome
from scoretopia.reports.dto import ReportDTO, ReportField
from scoretopia.reports.kinds import ReportKind
from scoretopia.reports.publisher import ReportPublisher
from scoretopia.screenshot.models import GameBasicsExtraction, GameEndExtraction
from scoretopia.storage.models import Game
from scoretopia.storage.repos import CaptureManifestRepo, GameRepo

logger = logging.getLogger(__name__)

KINDS = ("setup", "result")
REQUIRED_SCREENS = {
    "setup": ("card",),
    "result": ("setup", "victory"),
}
REPORTS_CHANNEL = "reports"
_START_LABELS = {
    "created": "Created by the bot",
    "invited": "Invited",
}
_UNKNOWN_START = "Created or invited: unknown"


@dataclass(frozen=True)
class CaptureManifest:
    directory: Path
    relative_path: str
    kind: str
    captured_at: str | None
    start: str
    screens: dict[str, Path]


@dataclass(frozen=True)
class SetupCapture:
    """What the setup screens say: the card panel, and the game id if read."""

    basics: GameBasicsExtraction
    game_id: str | None = None


@dataclass(frozen=True)
class ResultCapture:
    """What the result screens say: the replay's setup panel and victory screen."""

    basics: GameBasicsExtraction
    victory: GameEndExtraction
    game_id: str | None = None


class CaptureExtractor(Protocol):
    """Reads a capture's screens. Raises on a capture it cannot read."""

    def extract_setup(self, manifest: CaptureManifest) -> SetupCapture: ...

    def extract_result(self, manifest: CaptureManifest) -> ResultCapture: ...


@dataclass(frozen=True)
class CapturePost:
    report_name: str
    dto: ReportDTO
    channel_key: str = REPORTS_CHANNEL


def scan_manifests(root: Path) -> list[CaptureManifest]:
    """Every complete capture directory under ``root``, setups first.

    Within a kind, directories sort by ``captured_at`` then path, so a game's
    start is announced before its end and older games before newer ones.
    """
    manifests: list[CaptureManifest] = []
    for kind in KINDS:
        kind_dir = root / kind
        if not kind_dir.is_dir():
            continue
        for directory in sorted(kind_dir.iterdir()):
            if not directory.is_dir():
                continue
            manifest = _read_manifest(root, directory, kind)
            if manifest is not None:
                manifests.append(manifest)
    return sorted(
        manifests,
        key=lambda m: (KINDS.index(m.kind), m.captured_at or "", m.relative_path),
    )


def _read_manifest(root: Path, directory: Path, kind: str) -> CaptureManifest | None:
    relative = directory.relative_to(root).as_posix()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        raw = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        logger.warning("capture manifest unreadable directory=%s", relative)
        return None
    screens_raw = raw.get("screens") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or raw.get("kind") != kind
        or not isinstance(screens_raw, dict)
        or any(name not in screens_raw for name in REQUIRED_SCREENS[kind])
    ):
        logger.warning("capture manifest invalid directory=%s", relative)
        return None
    captured_at = raw.get("captured_at")
    start = raw.get("start")
    return CaptureManifest(
        directory=directory,
        relative_path=relative,
        kind=kind,
        captured_at=captured_at if isinstance(captured_at, str) else None,
        start=start if isinstance(start, str) else "unknown",
        screens={
            str(name): directory / str(file) for name, file in screens_raw.items()
        },
    )


class CaptureIngestService:
    """Turns new capture directories into matches and reports-channel posts."""

    def __init__(
        self,
        *,
        captures_path: Path,
        capture_repo: CaptureManifestRepo,
        game_repo: GameRepo,
        game_service: GameService,
        extractor: CaptureExtractor,
    ) -> None:
        self._root = captures_path
        self._captures = capture_repo
        self._games = game_repo
        self._game_service = game_service
        self._extractor = extractor

    def collect(self) -> list[CapturePost]:
        """Process every new capture directory once; return what to post."""
        posts: list[CapturePost] = []
        for manifest in scan_manifests(self._root):
            if self._captures.is_processed(manifest.relative_path):
                continue
            post = self._process(manifest)
            if post is not None:
                posts.append(post)
        return posts

    def run_once(self, publisher: ReportPublisher) -> int:
        """Process new captures and publish each post; return how many."""
        posts = self.collect()
        for post in posts:
            publisher.publish(post.report_name, post.dto, post.channel_key)
        return len(posts)

    def _process(self, manifest: CaptureManifest) -> CapturePost | None:
        handlers: dict[str, Callable[[CaptureManifest], CapturePost | None]] = {
            "setup": self._process_setup,
            "result": self._process_result,
        }
        try:
            return handlers[manifest.kind](manifest)
        except CaptureExtractionError as exc:
            logger.warning(
                "capture extraction failed directory=%s error=%s; retry next poll",
                manifest.relative_path,
                exc,
            )
        except Exception:
            logger.exception(
                "capture ingest failed directory=%s; retry next poll",
                manifest.relative_path,
            )
        return None

    def _process_setup(self, manifest: CaptureManifest) -> CapturePost | None:
        capture = _extract(self._extractor.extract_setup, manifest)
        if not capture.basics.players:
            raise CaptureExtractionError("no players read")
        if capture.game_id and self._captures.has_key("setup", capture.game_id):
            self._record_duplicate(manifest, capture.game_id)
            return None
        game = self._game_service.start_game(extraction=capture.basics)
        self._captures.record(
            directory=manifest.relative_path,
            kind="setup",
            game_key=capture.game_id,
            game_id=game.id,
            outcome="posted",
        )
        logger.info(
            "capture game started directory=%s game_id=%s",
            manifest.relative_path,
            game.id,
        )
        return CapturePost(
            "game_started",
            game_started_dto(game, capture.basics, start=manifest.start),
        )

    def _process_result(self, manifest: CaptureManifest) -> CapturePost | None:
        capture = _extract(self._extractor.extract_result, manifest)
        if not capture.victory.winner:
            raise CaptureExtractionError("no winner read")
        if not capture.basics.players and not capture.victory.players:
            raise CaptureExtractionError("no players read")
        if capture.game_id and self._captures.has_key("result", capture.game_id):
            self._record_duplicate(manifest, capture.game_id)
            return None
        game = self._match_for_result(capture)
        if game is None:
            # No setup capture was ever processed for this game: create it
            # from the replay's setup panel, without a start post.
            game = self._game_service.start_game(extraction=capture.basics)
        completed = self._game_service.complete_game(game.id, capture.victory).game
        self._captures.record(
            directory=manifest.relative_path,
            kind="result",
            game_key=capture.game_id,
            game_id=completed.id,
            outcome="posted",
        )
        logger.info(
            "capture game finished directory=%s game_id=%s",
            manifest.relative_path,
            completed.id,
        )
        return CapturePost(
            "game_finished",
            game_finished_dto(completed, capture.victory),
        )

    def _match_for_result(self, capture: ResultCapture) -> Game | None:
        if capture.game_id:
            game_id = self._captures.game_id_for_key("setup", capture.game_id)
            if game_id is not None:
                game = self._games.get_by_id(game_id)
                if game is not None and game.status == "active":
                    return game
        match = self._game_service.match_game_end(capture.victory)
        if match.outcome is MatchOutcome.NONE:
            return None
        if match.outcome is MatchOutcome.ONE:
            return match.games[0]
        named = [
            game
            for game in match.games
            if capture.basics.game_name and game.name == capture.basics.game_name
        ]
        candidates = named or list(match.games)
        return min(candidates, key=lambda game: game.id)

    def _record_duplicate(self, manifest: CaptureManifest, game_key: str) -> None:
        self._captures.record(
            directory=manifest.relative_path,
            kind=manifest.kind,
            game_key=game_key,
            game_id=None,
            outcome="duplicate",
        )
        logger.info(
            "capture duplicate of an earlier %s capture directory=%s",
            manifest.kind,
            manifest.relative_path,
        )


class CaptureExtractionError(ValueError):
    """A capture's screens could not be read into a usable extraction."""


def _extract[T](
    read: Callable[[CaptureManifest], T],
    manifest: CaptureManifest,
) -> T:
    try:
        return read(manifest)
    except CaptureExtractionError:
        raise
    except Exception as exc:
        raise CaptureExtractionError(type(exc).__name__) from exc


def game_started_dto(
    game: Game,
    basics: GameBasicsExtraction,
    *,
    start: str,
) -> ReportDTO:
    fields = [
        ReportField("Players", ", ".join(player.name for player in basics.players)),
        ReportField("Map", _map_summary(game)),
        ReportField("Mode", game.game_type or "unknown"),
    ]
    if game.target_score is not None:
        fields.append(ReportField("Points needed", str(game.target_score)))
    fields.append(ReportField("Start", _START_LABELS.get(start, _UNKNOWN_START)))
    return ReportDTO(
        title=f"Game started: {game.name}",
        description="",
        fields=fields,
        kind=ReportKind.game_started,
    )


def game_finished_dto(game: Game, victory: GameEndExtraction) -> ReportDTO:
    winner = victory.winner or "unknown"
    points = _winner_points(victory)
    return ReportDTO(
        title=f"Game finished: {game.name}",
        description="",
        fields=[
            ReportField("Winner", winner),
            ReportField("End points", str(points) if points is not None else "unknown"),
        ],
        kind=ReportKind.game_completed,
    )


def _map_summary(game: Game) -> str:
    parts: list[str] = []
    if game.map_size is not None:
        parts.append(f"{game.map_size} tiles")
    if game.terrain:
        parts.append(game.terrain)
    return ", ".join(parts) or "unknown"


def _winner_points(victory: GameEndExtraction) -> int | None:
    for player in victory.players:
        if player.is_winner and player.score is not None:
            return player.score
    if victory.winner:
        target = normalize_participant_name(victory.winner)
        for player in victory.players:
            if normalize_participant_name(player.name) == target:
                return player.score
    return None
