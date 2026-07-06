"""Tests for report scheduler and CLI entrypoints (Task 011)."""

from __future__ import annotations

import sqlite3
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scoretopia.config import (
    ChannelsConfig,
    DatabaseConfig,
    InboxConfig,
    ReportConfig,
    ScoretopiaConfig,
)
from scoretopia.reports.dto import ReportDTO
from scoretopia.reports.service import ReportService
from scoretopia.storage.db import open_database
from scoretopia.storage.models import GameParticipantInput
from scoretopia.storage.repos import (
    GameParticipantRepo,
    GameRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)


def _minimal_config(
    tmp_path: Path,
    *,
    active_enabled: bool = True,
    active_schedule: str = "0 9 * * *",
    recent_enabled: bool = False,
    win_enabled: bool = True,
) -> ScoretopiaConfig:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    db_path = tmp_path / "data" / "scoretopia.db"
    db_path.parent.mkdir(parents=True)
    inbox_path = tmp_path / "data" / "inbox"
    inbox_path.mkdir(parents=True)
    return ScoretopiaConfig(
        channels=ChannelsConfig(input="in", reports="out"),
        database=DatabaseConfig(path=db_path),
        inbox=InboxConfig(path=inbox_path),
        reports={
            "active_games": ReportConfig(
                enabled=active_enabled,
                schedule=active_schedule,
                channel="reports",
            ),
            "recent_completions": ReportConfig(
                enabled=recent_enabled,
                schedule="0 10 * * 1",
                channel="reports",
                lookback_days=14,
            ),
            "win_ratios": ReportConfig(
                enabled=win_enabled,
                schedule="0 8 1 * *",
                channel="reports",
            ),
        },
    )


def _write_config_file(tmp_path: Path, body: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "scoretopia.yaml"
    config_file.write_text(textwrap.dedent(body).strip() + "\n")
    return config_file


def _seed_active_game(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(str(db_path))
    try:
        player_repo = PlayerRepo(conn)
        game_repo = GameRepo(conn)
        participant_repo = GameParticipantRepo(conn)
        alice = player_repo.create(polytopia_name="Alice")
        bob = player_repo.create(polytopia_name="Bob")
        game = game_repo.create_active_game(
            name="Friday Night",
            map_size=12,
            terrain="Drylands",
        )
        participants = [
            GameParticipantInput(player_id=alice.id, is_bot=False),
            GameParticipantInput(player_id=bob.id, is_bot=False),
        ]
        participant_repo.add_participants(game.id, participants)
    finally:
        conn.close()


@dataclass
class RecordingPublisher:
    """Test double for ReportPublisher."""

    calls: list[tuple[str, ReportDTO, str]] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    def publish(self, report_name: str, dto: ReportDTO, channel_key: str) -> None:
        if report_name in self.fail_on:
            raise RuntimeError(f"publish failed for {report_name}")
        self.calls.append((report_name, dto, channel_key))


@pytest.fixture
def report_service(conn: sqlite3.Connection) -> ReportService:
    return ReportService(
        GameRepo(conn),
        GameParticipantRepo(conn),
        PlayerRepo(conn),
        PlayerPairRatioRepo(conn),
    )


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


def test_cli_report_run_name_active_games_prints_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = _write_config_file(
        tmp_path,
        """
        channels:
          input: in
          reports: out
        database:
          path: ../data/scoretopia.db
        inbox:
          path: ../data/inbox
        reports:
          active_games:
            enabled: true
            schedule: "0 9 * * *"
            channel: reports
          recent_completions:
            enabled: false
            schedule: "0 10 * * 1"
            channel: reports
            lookback_days: 14
          win_ratios:
            enabled: true
            schedule: "0 8 1 * *"
            channel: reports
        """,
    )
    _seed_active_game(config_file.parent.parent / "data" / "scoretopia.db")

    from scoretopia.cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "scoretopia",
            "report",
            "run",
            "--name",
            "active_games",
            "--config",
            str(config_file),
        ],
    )
    main()

    captured = capsys.readouterr()
    assert "Active Games" in captured.out
    assert "Friday Night" in captured.out
    assert "Alice" in captured.out
    assert "Bob" in captured.out


def test_cli_report_run_unknown_name_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = _write_config_file(tmp_path, _minimal_valid_yaml_body())

    from scoretopia.cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "scoretopia",
            "report",
            "run",
            "--name",
            "leaderboard",
            "--config",
            str(config_file),
        ],
    )

    with pytest.raises((ValueError, KeyError), match="leaderboard|Unknown"):
        main()


def test_run_due_reports_invokes_publisher_when_cron_is_due(
    report_service: ReportService,
    tmp_path: Path,
) -> None:
    from scoretopia.reports.scheduler import run_due_reports

    config = _minimal_config(tmp_path, active_schedule="0 9 * * *")
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)

    run_due_reports(config, report_service, publisher, now=now)

    names = [call[0] for call in publisher.calls]
    assert "active_games" in names


def test_run_due_reports_skips_reports_not_due(
    report_service: ReportService,
    tmp_path: Path,
) -> None:
    from scoretopia.reports.scheduler import run_due_reports

    config = _minimal_config(tmp_path, active_schedule="0 10 * * *")
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)

    run_due_reports(config, report_service, publisher, now=now)

    assert publisher.calls == []


def test_run_due_reports_skips_disabled_reports(
    report_service: ReportService,
    tmp_path: Path,
) -> None:
    from scoretopia.reports.scheduler import run_due_reports

    config = _minimal_config(
        tmp_path,
        active_enabled=False,
        active_schedule="* * * * *",
        win_enabled=False,
    )
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)

    run_due_reports(config, report_service, publisher, now=now, force_all=True)

    assert publisher.calls == []


def test_run_due_reports_force_all_runs_enabled_reports(
    report_service: ReportService,
    tmp_path: Path,
) -> None:
    from scoretopia.reports.scheduler import run_due_reports

    config = _minimal_config(
        tmp_path,
        active_schedule="0 10 * * *",
        win_enabled=True,
    )
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)

    run_due_reports(config, report_service, publisher, now=now, force_all=True)

    names = {call[0] for call in publisher.calls}
    assert "active_games" in names
    assert "win_ratios" in names
    assert "recent_completions" not in names


def test_run_due_reports_passes_channel_key_to_publisher(
    report_service: ReportService,
    tmp_path: Path,
) -> None:
    from scoretopia.reports.scheduler import run_due_reports

    config = _minimal_config(tmp_path, active_schedule="0 9 * * *")
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)

    run_due_reports(config, report_service, publisher, now=now)

    assert publisher.calls
    _, _, channel_key = publisher.calls[0]
    assert channel_key == "reports"


def test_run_due_reports_logs_success_and_failure(
    report_service: ReportService,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scoretopia.reports.scheduler import run_due_reports

    config = _minimal_config(tmp_path, active_schedule="0 9 * * *")
    publisher = RecordingPublisher(fail_on={"active_games"})
    now = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)

    with caplog.at_level("INFO"):
        run_due_reports(config, report_service, publisher, now=now)

    log_text = caplog.text.lower()
    assert "active_games" in log_text
    assert "failure" in log_text or "failed" in log_text or "error" in log_text


def test_scheduler_tick_invokes_publisher_with_short_interval_cron(
    report_service: ReportService,
    tmp_path: Path,
) -> None:
    from scoretopia.reports.scheduler import run_scheduler_tick

    config = _minimal_config(tmp_path, active_schedule="* * * * *")
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 5, 12, 34, 0, tzinfo=UTC)

    run_scheduler_tick(config, report_service, publisher, now=now)

    assert any(call[0] == "active_games" for call in publisher.calls)


def test_stdout_publisher_writes_formatted_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scoretopia.reports.publisher import StdoutReportPublisher

    dto = ReportDTO(
        title="Active Games",
        description="1 game(s) currently in progress.",
        fields=[],
    )
    publisher = StdoutReportPublisher()
    publisher.publish("active_games", dto, "reports")

    captured = capsys.readouterr()
    assert "Active Games" in captured.out


def _minimal_valid_yaml_body() -> str:
    return """
        channels:
          input: in
          reports: out
        database:
          path: ../data/scoretopia.db
        inbox:
          path: ../data/inbox
        reports:
          active_games:
            enabled: true
            schedule: "0 9 * * *"
            channel: reports
          recent_completions:
            enabled: false
            schedule: "0 10 * * 1"
            channel: reports
            lookback_days: 14
          win_ratios:
            enabled: true
            schedule: "0 8 1 * *"
            channel: reports
    """
