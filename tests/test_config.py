"""Tests for Scoretopia YAML configuration loader (Task 004)."""

from __future__ import annotations

import dataclasses
import textwrap
from pathlib import Path

import pytest

from scoretopia.config import ConfigError, ScoretopiaConfig, load_config

KNOWN_REPORTS = frozenset({"active_games", "recent_completions", "win_ratios"})


def _write_config(tmp_path: Path, body: str, *, subdir: str = "") -> Path:
    config_dir = tmp_path / subdir if subdir else tmp_path
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "scoretopia.yaml"
    config_file.write_text(textwrap.dedent(body).strip() + "\n")
    return config_file


def _minimal_valid_yaml(*, extra_reports: str = "") -> str:
    return f"""
        channels:
          input: polytopia-screenshots
          reports: polytopia-reports
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
            channel: input
        {extra_reports}
    """


def test_load_config_parses_valid_fixture(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path, _minimal_valid_yaml())
    config = load_config(config_file)

    assert isinstance(config, ScoretopiaConfig)
    assert config.channels.input == "polytopia-screenshots"
    assert config.channels.reports == "polytopia-reports"
    expected_db = config_file.parent / "data" / "scoretopia.db"
    assert config.database.path == expected_db.resolve()
    assert config.inbox.path == (config_file.parent / "data" / "inbox").resolve()

    assert set(config.reports) == KNOWN_REPORTS

    active_games = config.reports["active_games"]
    assert active_games.enabled is True
    assert active_games.schedule == "0 9 * * *"
    assert active_games.channel == "reports"

    recent = config.reports["recent_completions"]
    assert recent.enabled is False
    assert recent.schedule == "0 10 * * 1"
    assert recent.channel == "reports"
    assert recent.lookback_days == 14

    win_ratios = config.reports["win_ratios"]
    assert win_ratios.enabled is True
    assert win_ratios.schedule == "0 8 1 * *"
    assert win_ratios.channel == "input"


def test_load_config_resolves_explicit_relative_paths_from_config_dir(
    tmp_path: Path,
) -> None:
    config_file = _write_config(
        tmp_path,
        """
        channels:
          input: in
          reports: out
        database:
          path: data/custom.db
        inbox:
          path: data/custom-inbox
        reports:
          active_games:
            enabled: true
            schedule: "0 * * * *"
            channel: reports
          recent_completions:
            enabled: false
            schedule: "0 0 * * 0"
            channel: reports
          win_ratios:
            enabled: false
            schedule: "0 0 1 * *"
            channel: reports
        """,
        subdir="config",
    )
    config = load_config(config_file)

    assert config.database.path == (config_file.parent / "data" / "custom.db").resolve()
    assert config.inbox.path == (
        config_file.parent / "data" / "custom-inbox"
    ).resolve()


def test_load_config_default_path_none_uses_project_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    _write_config(project_root, _minimal_valid_yaml(), subdir="config")
    monkeypatch.chdir(project_root)

    config = load_config()

    assert config.channels.input == "polytopia-screenshots"
    expected_db = project_root / "config" / "data" / "scoretopia.db"
    assert config.database.path == expected_db.resolve()


def test_scoretopia_config_is_frozen(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path, _minimal_valid_yaml())
    config = load_config(config_file)

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.channels.input = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("yaml_body", "match"),
    [
        (
            """
            channels:
              reports: polytopia-reports
            reports:
              active_games:
                enabled: true
                schedule: "0 9 * * *"
                channel: reports
              recent_completions:
                enabled: false
                schedule: "0 10 * * 1"
                channel: reports
              win_ratios:
                enabled: true
                schedule: "0 8 1 * *"
                channel: input
            """,
            "channels.input",
        ),
        (
            """
            channels:
              input: polytopia-screenshots
            reports:
              active_games:
                enabled: true
                schedule: "0 9 * * *"
                channel: reports
              recent_completions:
                enabled: false
                schedule: "0 10 * * 1"
                channel: reports
              win_ratios:
                enabled: true
                schedule: "0 8 1 * *"
                channel: input
            """,
            "channels.reports",
        ),
        (
            """
            channels:
              input: polytopia-screenshots
              reports: polytopia-reports
            reports:
              active_games:
                schedule: "0 9 * * *"
                channel: reports
              recent_completions:
                enabled: false
                schedule: "0 10 * * 1"
                channel: reports
              win_ratios:
                enabled: true
                schedule: "0 8 1 * *"
                channel: input
            """,
            "enabled",
        ),
        (
            """
            channels:
              input: polytopia-screenshots
              reports: polytopia-reports
            reports:
              active_games:
                enabled: true
                channel: reports
              recent_completions:
                enabled: false
                schedule: "0 10 * * 1"
                channel: reports
              win_ratios:
                enabled: true
                schedule: "0 8 1 * *"
                channel: input
            """,
            "schedule",
        ),
        (
            """
            channels:
              input: polytopia-screenshots
              reports: polytopia-reports
            reports:
              active_games:
                enabled: true
                schedule: "0 9 * * *"
              recent_completions:
                enabled: false
                schedule: "0 10 * * 1"
                channel: reports
              win_ratios:
                enabled: true
                schedule: "0 8 1 * *"
                channel: input
            """,
            "channel",
        ),
    ],
)
def test_load_config_missing_required_keys_raises(
    tmp_path: Path, yaml_body: str, match: str
) -> None:
    config_file = _write_config(tmp_path, yaml_body)

    with pytest.raises(ConfigError, match=match):
        load_config(config_file)


def test_load_config_unknown_report_channel_raises(tmp_path: Path) -> None:
    config_file = _write_config(
        tmp_path,
        """
        channels:
          input: polytopia-screenshots
          reports: polytopia-reports
        reports:
          active_games:
            enabled: true
            schedule: "0 9 * * *"
            channel: announcements
          recent_completions:
            enabled: false
            schedule: "0 10 * * 1"
            channel: reports
          win_ratios:
            enabled: true
            schedule: "0 8 1 * *"
            channel: input
        """,
    )

    with pytest.raises(ConfigError, match="channel"):
        load_config(config_file)


def test_load_config_unknown_report_name_raises(tmp_path: Path) -> None:
    config_file = _write_config(
        tmp_path,
        _minimal_valid_yaml(
            extra_reports="""
          leaderboard:
            enabled: true
            schedule: "0 9 * * *"
            channel: reports
        """
        ),
    )

    with pytest.raises(ConfigError, match="leaderboard"):
        load_config(config_file)


@pytest.mark.parametrize(
    "schedule",
    ["", "   ", "\t"],
)
def test_load_config_empty_schedule_raises(tmp_path: Path, schedule: str) -> None:
    config_file = _write_config(
        tmp_path,
        f"""
        channels:
          input: polytopia-screenshots
          reports: polytopia-reports
        reports:
          active_games:
            enabled: true
            schedule: "{schedule}"
            channel: reports
          recent_completions:
            enabled: false
            schedule: "0 10 * * 1"
            channel: reports
          win_ratios:
            enabled: true
            schedule: "0 8 1 * *"
            channel: input
        """,
    )

    with pytest.raises(ConfigError, match="schedule"):
        load_config(config_file)


def test_load_config_missing_config_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(ConfigError, match="missing"):
        load_config(missing)


def _minimal_valid_yaml_with_bot_mods(
    *,
    discord_user_ids: str = '["123456789012345678"]',
    training_path: str | None = "data/training",
) -> str:
    training_block = ""
    if training_path is not None:
        training_block = f"""
        training:
          path: {training_path}
        """
    return (
        _minimal_valid_yaml()
        + f"""
        bot_mods:
          discord_user_ids: {discord_user_ids}
        """
        + training_block
    )


def test_load_config_parses_bot_mods_and_training(tmp_path: Path) -> None:
    from scoretopia.config import is_bot_mod

    config_file = _write_config(
        tmp_path,
        _minimal_valid_yaml_with_bot_mods(
            discord_user_ids='["111111111111111111", "222222222222222222"]',
            training_path="data/custom-training",
        ),
    )
    config = load_config(config_file)

    assert config.bot_mods.discord_user_ids == (
        "111111111111111111",
        "222222222222222222",
    )
    assert config.training.path == (
        config_file.parent / "data" / "custom-training"
    ).resolve()
    assert is_bot_mod("111111111111111111", config) is True
    assert is_bot_mod("999999999999999999", config) is False


def test_load_config_defaults_empty_bot_mods_and_training_path(tmp_path: Path) -> None:
    from scoretopia.config import is_bot_mod

    config_file = _write_config(tmp_path, _minimal_valid_yaml())
    config = load_config(config_file)

    assert config.bot_mods.discord_user_ids == ()
    assert config.training.path == (config_file.parent / "data" / "training").resolve()
    # Empty list means no mod bypass — every sensitive action queues approval.
    assert is_bot_mod("123456789012345678", config) is False


@pytest.mark.parametrize(
    ("discord_user_ids", "match"),
    [
        ("not-a-list", "bot_mods.discord_user_ids"),
        ('[""]', "bot_mods.discord_user_ids"),
        ('["abc"]', "bot_mods.discord_user_ids"),
        ('[123456789012345678]', "bot_mods.discord_user_ids"),
        ('["12 34"]', "bot_mods.discord_user_ids"),
    ],
)
def test_load_config_invalid_bot_mods_raises(
    tmp_path: Path, discord_user_ids: str, match: str
) -> None:
    config_file = _write_config(
        tmp_path,
        _minimal_valid_yaml_with_bot_mods(discord_user_ids=discord_user_ids),
    )

    with pytest.raises(ConfigError, match=match):
        load_config(config_file)


def test_load_config_invalid_training_path_raises(tmp_path: Path) -> None:
    config_file = _write_config(
        tmp_path,
        _minimal_valid_yaml()
        + """
        training:
          path: 42
        """,
    )

    with pytest.raises(ConfigError, match="training.path|Path"):
        load_config(config_file)
