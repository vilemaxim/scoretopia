"""Capture ingest: steam-test manifest directories → matches → reports posts.

Fixture manifests and a stubbed extractor stand in for the PNGs and OCR, so
no image is read here.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from scoretopia.domain.captures import (
    CaptureIngestService,
    CaptureManifest,
    ResultCapture,
    SetupCapture,
    count_unprocessed,
    scan_manifests,
    seed_captures,
)
from scoretopia.domain.games import GameService
from scoretopia.reports.dto import ReportDTO
from scoretopia.reports.kinds import ReportKind
from scoretopia.screenshot.models import (
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndPlayer,
)
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import (
    CaptureManifestRepo,
    GameParticipantRepo,
    GameRepo,
    PendingInteractionRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)

SETUP_SCREENS = {"card": "card.png", "info": "info.png"}
RESULT_SCREENS = {"setup": "setup.png", "info": "info.png", "victory": "victory.png"}


def _basics(name: str = "Test Game") -> GameBasicsExtraction:
    return GameBasicsExtraction(
        game_name=name,
        map_size=256,
        terrain="Lakes",
        target_score=10000,
        game_type="Glory",
        game_timer="24 hours",
        players=(GameBasicsPlayer(name="Alpha"), GameBasicsPlayer(name="Bravo")),
    )


def _victory(winner: str | None = "Alpha") -> GameEndExtraction:
    return GameEndExtraction(
        winner=winner,
        players=(
            GameEndPlayer(name="Alpha", score=10450, is_winner=winner == "Alpha"),
            GameEndPlayer(name="Bravo", score=7200, is_winner=winner == "Bravo"),
        ),
    )


def _write_manifest(
    root: Path,
    kind: str,
    key: str,
    *,
    captured_at: str = "2026-10-20T10:00:00Z",
    start: str = "unknown",
) -> Path:
    directory = root / kind / key
    directory.mkdir(parents=True)
    screens = SETUP_SCREENS if kind == "setup" else RESULT_SCREENS
    manifest = {
        "kind": kind,
        "captured_at": captured_at,
        "start": start,
        "screens": screens,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


class StubExtractor:
    """Answers per manifest directory name; a missing entry is a failed read."""

    def __init__(self) -> None:
        self.setups: dict[str, SetupCapture] = {}
        self.results: dict[str, ResultCapture] = {}
        self.calls: list[str] = []

    def extract_setup(self, manifest: CaptureManifest) -> SetupCapture:
        self.calls.append(manifest.directory.name)
        try:
            return self.setups[manifest.directory.name]
        except KeyError:
            raise ValueError("unreadable capture") from None

    def extract_result(self, manifest: CaptureManifest) -> ResultCapture:
        self.calls.append(manifest.directory.name)
        try:
            return self.results[manifest.directory.name]
        except KeyError:
            raise ValueError("unreadable capture") from None


class RecordingPublisher:
    def __init__(self) -> None:
        self.posts: list[tuple[str, ReportDTO, str]] = []

    def publish(self, report_name: str, dto: ReportDTO, channel_key: str) -> None:
        self.posts.append((report_name, dto, channel_key))


@pytest.fixture
def conn() -> sqlite3.Connection:
    return open_database(":memory:")


def _service(
    conn: sqlite3.Connection,
    root: Path,
    extractor: StubExtractor,
) -> CaptureIngestService:
    game_service = GameService(
        GameRepo(conn),
        GameParticipantRepo(conn),
        PlayerRepo(conn),
        pending_repo=PendingInteractionRepo(conn),
        ratio_repo=PlayerPairRatioRepo(conn),
    )
    return CaptureIngestService(
        captures_path=root,
        capture_repo=CaptureManifestRepo(conn),
        game_repo=GameRepo(conn),
        game_service=game_service,
        extractor=extractor,
    )


def _fields(dto: ReportDTO) -> dict[str, str]:
    return {field.label: field.value for field in dto.fields}


# --- scanning --------------------------------------------------------------


def test_scan_reads_manifest_fields_and_resolves_screens(tmp_path: Path) -> None:
    directory = _write_manifest(tmp_path, "setup", "k1", start="created")

    [manifest] = scan_manifests(tmp_path)

    assert manifest.kind == "setup"
    assert manifest.start == "created"
    assert manifest.captured_at == "2026-10-20T10:00:00Z"
    assert manifest.directory == directory
    assert manifest.relative_path == "setup/k1"
    assert manifest.screens == {
        "card": directory / "card.png",
        "info": directory / "info.png",
    }


def test_scan_orders_setups_before_results_then_by_capture_time(
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "result", "r1", captured_at="2026-10-20T09:00:00Z")
    _write_manifest(tmp_path, "setup", "s2", captured_at="2026-10-21T10:00:00Z")
    _write_manifest(tmp_path, "setup", "s1", captured_at="2026-10-20T10:00:00Z")

    order = [manifest.relative_path for manifest in scan_manifests(tmp_path)]

    assert order == ["setup/s1", "setup/s2", "result/r1"]


def test_scan_skips_directories_without_a_valid_manifest(tmp_path: Path) -> None:
    (tmp_path / "setup" / "in-progress").mkdir(parents=True)
    broken = tmp_path / "setup" / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json")
    wrong_kind = tmp_path / "setup" / "wrong"
    wrong_kind.mkdir(parents=True)
    (wrong_kind / "manifest.json").write_text(
        json.dumps({"kind": "result", "screens": RESULT_SCREENS})
    )
    (tmp_path / "record.json").write_text("{}")

    assert scan_manifests(tmp_path) == []


def test_scan_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert scan_manifests(tmp_path / "absent") == []


# --- game started ----------------------------------------------------------


def test_new_setup_manifest_creates_match_and_posts_game_started(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1", start="invited")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    publisher = RecordingPublisher()

    posted = _service(conn, tmp_path, extractor).run_once(publisher)

    assert posted == 1
    [(name, dto, channel)] = publisher.posts
    assert (name, channel) == ("game_started", "reports")
    assert dto.kind is ReportKind.game_started
    assert dto.title == "Game started: Test Game"
    fields = _fields(dto)
    assert fields["Players"] == "Alpha, Bravo"
    assert fields["Map"] == "256 tiles, Lakes"
    assert fields["Mode"] == "Glory"
    assert fields["Points needed"] == "10000"
    assert fields["Start"] == "Invited"
    [game] = GameRepo(conn).list_active()
    assert game.name == "Test Game"


def test_unknown_start_is_said_plainly(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    publisher = RecordingPublisher()

    _service(conn, tmp_path, extractor).run_once(publisher)

    assert _fields(publisher.posts[0][1])["Start"] == "Created or invited: unknown"


def test_second_pass_posts_nothing_and_extracts_nothing(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    service = _service(conn, tmp_path, extractor)
    publisher = RecordingPublisher()

    service.run_once(publisher)
    assert service.run_once(publisher) == 0

    assert len(publisher.posts) == 1
    assert extractor.calls == ["s1"]
    assert len(GameRepo(conn).list_active()) == 1


def test_processed_manifests_survive_a_new_service(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    _service(conn, tmp_path, extractor).run_once(RecordingPublisher())

    publisher = RecordingPublisher()
    _service(conn, tmp_path, extractor).run_once(publisher)

    assert publisher.posts == []


def test_failed_extraction_posts_nothing_and_is_retried_next_poll(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    extractor = StubExtractor()
    service = _service(conn, tmp_path, extractor)
    publisher = RecordingPublisher()

    assert service.run_once(publisher) == 0
    assert publisher.posts == []
    assert GameRepo(conn).list_active() == []

    extractor.setups["s1"] = SetupCapture(basics=_basics())
    assert service.run_once(publisher) == 1
    assert extractor.calls == ["s1", "s1"]


def test_setup_with_no_players_counts_as_a_failed_extraction(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=GameBasicsExtraction())
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 0
    assert GameRepo(conn).list_active() == []


def test_one_bad_manifest_does_not_stop_the_others(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "bad", captured_at="2026-10-20T09:00:00Z")
    _write_manifest(tmp_path, "setup", "good", captured_at="2026-10-20T10:00:00Z")
    extractor = StubExtractor()
    extractor.setups["good"] = SetupCapture(basics=_basics())
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 1


def test_same_game_id_in_a_second_setup_directory_is_not_posted_twice(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1", captured_at="2026-10-20T10:00:00Z")
    _write_manifest(tmp_path, "setup", "s2", captured_at="2026-10-21T10:00:00Z")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics(), game_id="g-1")
    extractor.setups["s2"] = SetupCapture(basics=_basics(), game_id="g-1")
    service = _service(conn, tmp_path, extractor)
    publisher = RecordingPublisher()

    assert service.run_once(publisher) == 1
    assert len(GameRepo(conn).list_active()) == 1
    assert service.run_once(publisher) == 0
    assert extractor.calls == ["s1", "s2"]


def test_without_a_game_id_each_setup_directory_counts(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1", captured_at="2026-10-20T10:00:00Z")
    _write_manifest(tmp_path, "setup", "s2", captured_at="2026-10-21T10:00:00Z")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics("One"))
    extractor.setups["s2"] = SetupCapture(basics=_basics("Two"))
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 2


# --- game finished ---------------------------------------------------------


def test_result_completes_the_matched_game_and_posts_game_finished(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    _write_manifest(tmp_path, "result", "r1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    extractor.results["r1"] = ResultCapture(basics=_basics(), victory=_victory())
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 2

    names = [name for name, _, _ in publisher.posts]
    assert names == ["game_started", "game_finished"]
    finished = publisher.posts[1][1]
    assert finished.kind is ReportKind.game_completed
    assert finished.title == "Game finished: Test Game"
    assert _fields(finished)["Winner"] == "Alpha"
    assert _fields(finished)["End points"] == "10450"
    games = GameRepo(conn)
    assert games.list_active() == []
    completed = games.get_by_id(1)
    assert completed is not None
    assert completed.status == "completed"
    winner = PlayerRepo(conn).get_by_id(completed.winner_player_id or 0)
    assert winner is not None and winner.polytopia_name == "Alpha"


def test_result_records_end_points_for_every_player(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    _write_manifest(tmp_path, "result", "r1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    extractor.results["r1"] = ResultCapture(basics=_basics(), victory=_victory())

    _service(conn, tmp_path, extractor).run_once(RecordingPublisher())

    scores = dict(
        conn.execute(
            """
            SELECT p.polytopia_name, gp.score FROM game_participants gp
            JOIN players p ON p.id = gp.player_id
            """
        ).fetchall()
    )
    assert scores == {"Alpha": 10450, "Bravo": 7200}


def test_result_with_no_setup_seen_creates_and_completes_without_a_start_post(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "result", "r1")
    extractor = StubExtractor()
    extractor.results["r1"] = ResultCapture(basics=_basics(), victory=_victory())
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 1

    assert [name for name, _, _ in publisher.posts] == ["game_finished"]
    game = GameRepo(conn).get_by_id(1)
    assert game is not None and game.status == "completed"


def test_result_matches_its_setup_by_game_id(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1", captured_at="2026-10-20T10:00:00Z")
    _write_manifest(tmp_path, "setup", "s2", captured_at="2026-10-21T10:00:00Z")
    _write_manifest(tmp_path, "result", "r1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics("Same"), game_id="g-1")
    extractor.setups["s2"] = SetupCapture(basics=_basics("Same"), game_id="g-2")
    extractor.results["r1"] = ResultCapture(
        basics=_basics("Same"), victory=_victory(), game_id="g-2"
    )

    _service(conn, tmp_path, extractor).run_once(RecordingPublisher())

    games = GameRepo(conn)
    first, second = games.get_by_id(1), games.get_by_id(2)
    assert first is not None and first.status == "active"
    assert second is not None and second.status == "completed"


def test_result_without_game_id_prefers_the_active_game_with_its_name(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1", captured_at="2026-10-20T10:00:00Z")
    _write_manifest(tmp_path, "setup", "s2", captured_at="2026-10-21T10:00:00Z")
    _write_manifest(tmp_path, "result", "r1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics("One"))
    extractor.setups["s2"] = SetupCapture(basics=_basics("Two"))
    extractor.results["r1"] = ResultCapture(basics=_basics("Two"), victory=_victory())

    _service(conn, tmp_path, extractor).run_once(RecordingPublisher())

    games = GameRepo(conn)
    one, two = games.get_by_id(1), games.get_by_id(2)
    assert one is not None and one.status == "active"
    assert two is not None and two.status == "completed"


def test_same_game_id_in_a_second_result_directory_is_not_posted_twice(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "result", "r1", captured_at="2026-10-20T10:00:00Z")
    _write_manifest(tmp_path, "result", "r2", captured_at="2026-10-21T10:00:00Z")
    extractor = StubExtractor()
    for key in ("r1", "r2"):
        extractor.results[key] = ResultCapture(
            basics=_basics(), victory=_victory(), game_id="g-1"
        )
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 1
    assert len(conn.execute("SELECT id FROM games").fetchall()) == 1


def test_result_with_no_winner_counts_as_a_failed_extraction(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "result", "r1")
    extractor = StubExtractor()
    extractor.results["r1"] = ResultCapture(
        basics=_basics(), victory=_victory(winner=None)
    )
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 0
    assert conn.execute("SELECT id FROM games").fetchall() == []


def test_collect_returns_posts_without_publishing(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())

    posts = _service(conn, tmp_path, extractor).collect()

    assert [post.report_name for post in posts] == ["game_started"]
    assert posts[0].channel_key == "reports"


# --- bounded runs ----------------------------------------------------------


def _three_setups(root: Path) -> StubExtractor:
    extractor = StubExtractor()
    for index, key in enumerate(("s1", "s2", "s3"), start=1):
        _write_manifest(root, "setup", key, captured_at=f"2026-10-2{index}T10:00:00Z")
        extractor.setups[key] = SetupCapture(basics=_basics(f"Game {index}"))
    return extractor


def test_limit_stops_after_n_and_the_next_run_continues(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    extractor = _three_setups(tmp_path)
    service = _service(conn, tmp_path, extractor)
    publisher = RecordingPublisher()

    assert service.run_once(publisher, limit=2) == 2
    assert extractor.calls == ["s1", "s2"]
    assert service.remaining() == 1

    assert service.run_once(publisher, limit=2) == 1
    assert extractor.calls == ["s1", "s2", "s3"]
    assert service.remaining() == 0
    assert [dto.title for _, dto, _ in publisher.posts] == [
        "Game started: Game 1",
        "Game started: Game 2",
        "Game started: Game 3",
    ]


def test_a_failed_extraction_counts_toward_the_limit_and_stays_unprocessed(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    extractor = _three_setups(tmp_path)
    del extractor.setups["s1"]
    service = _service(conn, tmp_path, extractor)

    assert service.run_once(RecordingPublisher(), limit=2) == 1
    assert extractor.calls == ["s1", "s2"]
    assert service.remaining() == 2
    assert not CaptureManifestRepo(conn).is_processed("setup/s1")

    assert service.run_once(RecordingPublisher(), limit=1) == 0
    assert extractor.calls == ["s1", "s2", "s1"]


def test_a_duplicate_counts_toward_the_limit(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    extractor = _three_setups(tmp_path)
    extractor.setups["s1"] = SetupCapture(basics=_basics(), game_id="g-1")
    extractor.setups["s2"] = SetupCapture(basics=_basics(), game_id="g-1")
    service = _service(conn, tmp_path, extractor)

    assert service.run_once(RecordingPublisher(), limit=2) == 1
    assert extractor.calls == ["s1", "s2"]
    assert service.remaining() == 1


def test_no_limit_processes_everything(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    extractor = _three_setups(tmp_path)

    assert _service(conn, tmp_path, extractor).run_once(RecordingPublisher()) == 3


@pytest.mark.parametrize("limit", [0, -1])
def test_limit_must_be_positive(
    conn: sqlite3.Connection,
    tmp_path: Path,
    limit: int,
) -> None:
    extractor = _three_setups(tmp_path)

    with pytest.raises(ValueError, match="positive"):
        _service(conn, tmp_path, extractor).collect(limit)
    assert extractor.calls == []


# --- seed ------------------------------------------------------------------


def test_seed_records_every_complete_unprocessed_directory(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1")
    _write_manifest(tmp_path, "result", "r1")
    (tmp_path / "setup" / "in-progress").mkdir()
    repo = CaptureManifestRepo(conn)

    assert seed_captures(tmp_path, repo) == 2

    rows = conn.execute(
        """
        SELECT directory, kind, game_key, game_id, outcome
        FROM capture_manifests ORDER BY directory
        """
    ).fetchall()
    assert rows == [
        ("result/r1", "result", None, None, "seeded"),
        ("setup/s1", "setup", None, None, "seeded"),
    ]
    assert not repo.is_processed("setup/in-progress")
    assert count_unprocessed(tmp_path, repo) == 0
    assert conn.execute("SELECT id FROM games").fetchall() == []


def test_seed_leaves_processed_directories_as_they_were(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, "setup", "s1", captured_at="2026-10-20T10:00:00Z")
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    _service(conn, tmp_path, extractor).run_once(RecordingPublisher())
    _write_manifest(tmp_path, "setup", "s2", captured_at="2026-10-21T10:00:00Z")

    assert seed_captures(tmp_path, CaptureManifestRepo(conn)) == 1
    assert seed_captures(tmp_path, CaptureManifestRepo(conn)) == 0

    outcomes = dict(conn.execute("SELECT directory, outcome FROM capture_manifests"))
    assert outcomes == {"setup/s1": "posted", "setup/s2": "seeded"}


def test_a_run_after_seed_posts_nothing_and_reads_nothing(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    extractor = _three_setups(tmp_path)
    seed_captures(tmp_path, CaptureManifestRepo(conn))
    publisher = RecordingPublisher()

    assert _service(conn, tmp_path, extractor).run_once(publisher) == 0
    assert publisher.posts == []
    assert extractor.calls == []


# --- one-shot CLI ----------------------------------------------------------


def _write_config(tmp_path: Path, captures: Path) -> Path:
    config_file = tmp_path / "scoretopia.yaml"
    config_file.write_text(
        f"""
channels:
  reports: polytopia-reports
captures:
  path: {captures}
reports:
  active_games: {{enabled: true, schedule: "0 9 * * *", channel: reports}}
  recent_completions: {{enabled: false, schedule: "0 10 * * 1", channel: reports}}
  win_ratios: {{enabled: false, schedule: "0 8 1 * *", channel: reports}}
"""
    )
    return config_file


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["scoretopia", *argv])
    from scoretopia.cli import main

    main()


def test_cli_captures_run_processes_the_directory_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captures = tmp_path / "captures"
    _write_manifest(captures, "setup", "s1")
    config_file = _write_config(tmp_path, captures)
    extractor = StubExtractor()
    extractor.setups["s1"] = SetupCapture(basics=_basics())
    monkeypatch.setattr(
        "scoretopia.screenshot.captures.OcrCaptureExtractor",
        lambda: extractor,
    )
    _run_cli(monkeypatch, "captures", "run", "--config", str(config_file))
    first = capsys.readouterr().out
    _run_cli(monkeypatch, "captures", "run", "--config", str(config_file))
    second = capsys.readouterr().out

    assert first.count("Game started: Test Game") == 1
    assert "Players: Alpha, Bravo" in first
    assert second == ""
    assert (tmp_path / "data" / "scoretopia.db").is_file()


def test_cli_captures_run_limit_prints_processed_and_remaining(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captures = tmp_path / "captures"
    extractor = _three_setups(captures)
    config_file = _write_config(tmp_path, captures)
    monkeypatch.setattr(
        "scoretopia.screenshot.captures.OcrCaptureExtractor",
        lambda: extractor,
    )

    _run_cli(
        monkeypatch, "captures", "run", "--limit", "2", "--config", str(config_file)
    )
    first = capsys.readouterr()
    _run_cli(monkeypatch, "captures", "run", "--config", str(config_file))
    second = capsys.readouterr()

    assert first.out.count("Game started:") == 2
    assert first.err.splitlines()[-2:] == [
        "Captures processed: 2 posted",
        "Captures remaining: 1",
    ]
    assert second.out.count("Game started:") == 1
    assert second.err.splitlines()[-2:] == [
        "Captures processed: 1 posted",
        "Captures remaining: 0",
    ]


@pytest.mark.parametrize("value", ["0", "-3", "two"])
def test_cli_captures_run_rejects_a_limit_that_is_not_positive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    config_file = _write_config(tmp_path, tmp_path / "captures")

    with pytest.raises(SystemExit) as exc:
        _run_cli(
            monkeypatch,
            "captures",
            "run",
            "--limit",
            value,
            "--config",
            str(config_file),
        )
    assert exc.value.code == 2


def test_cli_captures_seed_never_loads_ocr_and_a_run_after_it_posts_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captures = tmp_path / "captures"
    extractor = _three_setups(captures)
    (captures / "result" / "in-progress").mkdir(parents=True)
    config_file = _write_config(tmp_path, captures)
    # Any import of the OCR capture module during seed fails the test.
    monkeypatch.setitem(sys.modules, "scoretopia.screenshot.captures", None)

    _run_cli(monkeypatch, "captures", "seed", "--config", str(config_file))
    seeded = capsys.readouterr()

    assert seeded.out == ""
    assert seeded.err.splitlines()[-1] == "Captures seeded: 3"

    monkeypatch.delitem(sys.modules, "scoretopia.screenshot.captures")
    monkeypatch.setattr(
        "scoretopia.screenshot.captures.OcrCaptureExtractor",
        lambda: extractor,
    )
    _run_cli(monkeypatch, "captures", "run", "--config", str(config_file))
    after = capsys.readouterr()

    assert after.out == ""
    assert after.err.splitlines()[-2:] == [
        "Captures processed: 0 posted",
        "Captures remaining: 0",
    ]
    assert extractor.calls == []
