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
            channel: reports
        {extra_reports}
    """


def test_load_config_parses_valid_fixture(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path, _minimal_valid_yaml())
    config = load_config(config_file)

    assert isinstance(config, ScoretopiaConfig)
    assert config.channels.reports == "polytopia-reports"
    expected_db = config_file.parent / "data" / "scoretopia.db"
    assert config.database.path == expected_db.resolve()

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
    assert win_ratios.channel == "reports"


def test_load_config_resolves_explicit_relative_paths_from_config_dir(
    tmp_path: Path,
) -> None:
    config_file = _write_config(
        tmp_path,
        """
        channels:
          reports: out
        database:
          path: data/custom.db
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


def test_load_config_default_path_none_uses_project_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    _write_config(project_root, _minimal_valid_yaml(), subdir="config")
    monkeypatch.chdir(project_root)

    config = load_config()

    assert config.channels.reports == "polytopia-reports"
    expected_db = project_root / "config" / "data" / "scoretopia.db"
    assert config.database.path == expected_db.resolve()


def test_scoretopia_config_is_frozen(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path, _minimal_valid_yaml())
    config = load_config(config_file)

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.channels.reports = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("yaml_body", "match"),
    [
        (
            """
            channels: {}
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
                channel: reports
            """,
            "channels.reports",
        ),
        (
            """
            channels:
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
                channel: reports
            """,
            "enabled",
        ),
        (
            """
            channels:
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
                channel: reports
            """,
            "schedule",
        ),
        (
            """
            channels:
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
                channel: reports
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
            channel: reports
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
            channel: reports
        """,
    )

    with pytest.raises(ConfigError, match="schedule"):
        load_config(config_file)


def test_load_config_missing_config_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(ConfigError, match="missing"):
        load_config(missing)


def test_load_config_ignores_keys_only_the_review_flow_read(
    tmp_path: Path,
) -> None:
    config_file = _write_config(
        tmp_path,
        _minimal_valid_yaml()
        + """
        inbox:
          path: data/inbox
        training:
          path: data/training
        bot_mods:
          discord_user_ids: ["123456789012345678"]
        """,
    )

    config = load_config(config_file)

    assert not hasattr(config, "inbox")
    assert not hasattr(config, "training")
    assert not hasattr(config, "bot_mods")


def test_load_config_rejects_the_removed_input_channel(tmp_path: Path) -> None:
    body = _minimal_valid_yaml().replace(
        "channels:\n", "channels:\n          input: polytopia-screenshots\n", 1
    )
    config_file = _write_config(tmp_path, body)

    with pytest.raises(ConfigError, match="channels.input was removed"):
        load_config(config_file)


def test_load_config_rejects_a_report_on_the_input_channel(tmp_path: Path) -> None:
    body = _minimal_valid_yaml().replace(
        'schedule: "0 8 1 * *"\n            channel: reports',
        'schedule: "0 8 1 * *"\n            channel: input',
    )
    config_file = _write_config(tmp_path, body)

    with pytest.raises(ConfigError, match="unknown channel key: input"):
        load_config(config_file)


def test_load_config_captures_defaults(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path, _minimal_valid_yaml())
    config = load_config(config_file)

    assert config.captures.path == (config_file.parent / "data" / "captures").resolve()
    assert config.captures.poll_seconds == 300


def test_load_config_parses_captures(tmp_path: Path) -> None:
    body = _minimal_valid_yaml() + """
        captures:
          path: /srv/captures
          poll_seconds: 60
    """
    config_file = _write_config(tmp_path, body)
    config = load_config(config_file)

    assert config.captures.path == Path("/srv/captures")
    assert config.captures.poll_seconds == 60


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ("captures: []", "captures must be a mapping"),
        ("captures:\n          poll_seconds: 0", "captures.poll_seconds"),
        ("captures:\n          poll_seconds: soon", "captures.poll_seconds"),
        ("captures:\n          poll_seconds: true", "captures.poll_seconds"),
    ],
)
def test_load_config_rejects_bad_captures(
    tmp_path: Path, block: str, match: str
) -> None:
    config_file = _write_config(tmp_path, _minimal_valid_yaml() + "\n        " + block)

    with pytest.raises(ConfigError, match=match):
        load_config(config_file)


def test_load_config_discord_guild_id_defaults_to_none(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, _minimal_valid_yaml()))

    assert config.discord.guild_id is None


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("discord:", None),
        ("discord: {}", None),
        ("discord:\n          guild_id:", None),
        ('discord:\n          guild_id: "123456789012345678"', 123456789012345678),
        ("discord:\n          guild_id: 123456789012345678", 123456789012345678),
    ],
)
def test_load_config_parses_discord_guild_id(
    tmp_path: Path, block: str, expected: int | None
) -> None:
    body = _minimal_valid_yaml() + "\n        " + block
    config = load_config(_write_config(tmp_path, body))

    assert config.discord.guild_id == expected
    if expected is not None:
        assert isinstance(config.discord.guild_id, int)


@pytest.mark.parametrize(
    "value",
    ["true", '""', '"abc"', '"12 34"', '"-5"', "-5", "0", "[1, 2]", "1.5"],
)
def test_load_config_rejects_bad_discord_guild_id(
    tmp_path: Path, value: str
) -> None:
    body = _minimal_valid_yaml() + f"\n        discord:\n          guild_id: {value}"
    config_file = _write_config(tmp_path, body)

    with pytest.raises(ConfigError) as excinfo:
        load_config(config_file)

    assert str(excinfo.value) == "discord.guild_id must be a Discord server id (digits)"


@pytest.mark.parametrize("block", ["discord: abc", "discord: [1]"])
def test_load_config_rejects_a_non_mapping_discord_block(
    tmp_path: Path, block: str
) -> None:
    body = _minimal_valid_yaml() + "\n        " + block
    config_file = _write_config(tmp_path, body)

    with pytest.raises(ConfigError, match="discord must be a mapping"):
        load_config(config_file)
