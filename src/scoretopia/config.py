"""Scoretopia configuration loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

KNOWN_REPORTS = frozenset({"active_games", "recent_completions", "win_ratios"})
CHANNEL_KEYS = frozenset({"input", "reports"})
DEFAULT_CONFIG_PATH = Path("config/scoretopia.yaml")
DEFAULT_DATABASE_PATH = Path("data/scoretopia.db")
DEFAULT_INBOX_PATH = Path("data/inbox")


class ConfigError(ValueError):
    """Raised when configuration is missing, invalid, or inconsistent."""


@dataclass(frozen=True)
class ChannelsConfig:
    input: str
    reports: str


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path


@dataclass(frozen=True)
class InboxConfig:
    path: Path


@dataclass(frozen=True)
class ReportConfig:
    enabled: bool
    schedule: str
    channel: str
    lookback_days: int | None = None


@dataclass(frozen=True)
class ScoretopiaConfig:
    channels: ChannelsConfig
    database: DatabaseConfig
    inbox: InboxConfig
    reports: dict[str, ReportConfig]


def load_config(path: Path | None = None) -> ScoretopiaConfig:
    """Load and validate Scoretopia YAML configuration.

    Relative paths in ``database.path`` and ``inbox.path`` resolve against the
    directory containing the config file (not the process working directory).
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigError(f"Config file missing: {config_path}")

    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    return _parse_config(raw, config_path.parent)


def _parse_config(raw: dict[str, Any], base_dir: Path) -> ScoretopiaConfig:
    channels_raw = _require_mapping(raw, "channels")
    channels = ChannelsConfig(
        input=_require_str(channels_raw, "input", "channels.input"),
        reports=_require_str(channels_raw, "reports", "channels.reports"),
    )

    database_raw = raw.get("database", {})
    if database_raw is None:
        database_raw = {}
    if not isinstance(database_raw, dict):
        raise ConfigError("database must be a mapping")
    database_path = _resolve_path(
        base_dir,
        database_raw.get("path", DEFAULT_DATABASE_PATH),
    )

    inbox_raw = raw.get("inbox", {})
    if inbox_raw is None:
        inbox_raw = {}
    if not isinstance(inbox_raw, dict):
        raise ConfigError("inbox must be a mapping")
    inbox_path = _resolve_path(
        base_dir,
        inbox_raw.get("path", DEFAULT_INBOX_PATH),
    )

    reports_raw = _require_mapping(raw, "reports")
    reports = _parse_reports(reports_raw, channels_raw)

    return ScoretopiaConfig(
        channels=channels,
        database=DatabaseConfig(path=database_path),
        inbox=InboxConfig(path=inbox_path),
        reports=reports,
    )


def _parse_reports(
    reports_raw: dict[str, Any],
    channels_raw: dict[str, Any],
) -> dict[str, ReportConfig]:
    unknown = set(reports_raw) - KNOWN_REPORTS
    if unknown:
        name = sorted(unknown)[0]
        raise ConfigError(f"Unknown report name: {name}")

    missing = KNOWN_REPORTS - set(reports_raw)
    if missing:
        name = sorted(missing)[0]
        raise ConfigError(f"Missing required report definition: {name}")

    parsed: dict[str, ReportConfig] = {}
    for name in sorted(reports_raw):
        entry = reports_raw[name]
        if not isinstance(entry, dict):
            raise ConfigError(f"reports.{name} must be a mapping")
        parsed[name] = _parse_report(name, entry, channels_raw)
    return parsed


def _parse_report(
    name: str,
    entry: dict[str, Any],
    channels_raw: dict[str, Any],
) -> ReportConfig:
    prefix = f"reports.{name}"

    if "enabled" not in entry:
        raise ConfigError(f"Missing required key: {prefix}.enabled")
    enabled = entry["enabled"]
    if not isinstance(enabled, bool):
        raise ConfigError(f"{prefix}.enabled must be a boolean")

    if "schedule" not in entry:
        raise ConfigError(f"Missing required key: {prefix}.schedule")
    schedule = entry["schedule"]
    if not isinstance(schedule, str) or not schedule.strip():
        raise ConfigError(f"{prefix}.schedule must be a non-empty string")

    if "channel" not in entry:
        raise ConfigError(f"Missing required key: {prefix}.channel")
    channel = entry["channel"]
    if not isinstance(channel, str) or not channel:
        raise ConfigError(f"{prefix}.channel must be a non-empty string")
    if channel not in CHANNEL_KEYS:
        raise ConfigError(
            f"{prefix}.channel references unknown channel key: {channel}"
        )
    if channel not in channels_raw:
        raise ConfigError(f"Missing required key: channels.{channel}")

    lookback_days: int | None = None
    if "lookback_days" in entry:
        lookback_days = entry["lookback_days"]
        if not isinstance(lookback_days, int):
            raise ConfigError(f"{prefix}.lookback_days must be an integer")

    return ReportConfig(
        enabled=enabled,
        schedule=schedule,
        channel=channel,
        lookback_days=lookback_days,
    )


def _require_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in raw:
        raise ConfigError(f"Missing required key: {key}")
    value = raw[key]
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _require_str(raw: dict[str, Any], key: str, label: str) -> str:
    if key not in raw:
        raise ConfigError(f"Missing required key: {label}")
    value = raw[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} must be a non-empty string")
    return value


def _resolve_path(base_dir: Path, value: Any) -> Path:
    if not isinstance(value, (str, Path)):
        raise ConfigError("Path values must be strings")
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()
