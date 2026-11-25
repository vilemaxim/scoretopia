"""Seat control's own configuration file.

Seat control runs as a different user from recording and reads a different
file, so that it never holds the Discord token or the database (ADR 003). A
key this loader does not know is an error: a recording setting pasted here
is refused instead of ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scoretopia.config import ConfigError

DEFAULT_SEAT_CONFIG_PATH = Path("config/seat.yaml")
DEFAULT_FRAMES_PATH = Path("data/seat-frames")
DEFAULT_CAPTURES_PATH = Path("data/captures")
DEFAULT_BUDGET_SECONDS = 3300
DEFAULT_DISPLAY = ":99"
DEFAULT_X_USER = "steam"

_KEYS = frozenset({"frames", "captures", "budget_seconds", "display", "x_user"})


@dataclass(frozen=True)
class SeatConfig:
    #: Where the frame of an unknown screen is saved.
    frames_path: Path
    #: Where captures are written, for recording to read.
    captures_path: Path = DEFAULT_CAPTURES_PATH
    #: Seconds a command may spend before it stops itself.
    budget_seconds: int = DEFAULT_BUDGET_SECONDS
    #: The X display the game client runs on.
    display: str = DEFAULT_DISPLAY
    #: The user X commands run as when the command itself runs as root.
    x_user: str = DEFAULT_X_USER


def load_seat_config(path: Path | None = None) -> SeatConfig:
    """Load and validate seat control's YAML configuration.

    A relative ``frames.path`` or ``captures.path`` resolves against the
    directory containing the file.
    """
    config_path = Path(path) if path is not None else DEFAULT_SEAT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigError(f"Seat config file missing: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Seat config file unreadable: {config_path}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("Seat config root must be a mapping")

    unknown = sorted(str(key) for key in raw if key not in _KEYS)
    if unknown:
        raise ConfigError(f"Unknown seat config key: {unknown[0]}")

    frames_path = _path(raw, "frames", DEFAULT_FRAMES_PATH, config_path.parent)
    captures_path = _path(raw, "captures", DEFAULT_CAPTURES_PATH, config_path.parent)

    budget = raw.get("budget_seconds", DEFAULT_BUDGET_SECONDS)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ConfigError("budget_seconds must be a positive integer")
    if budget >= 3600:
        raise ConfigError("budget_seconds must be shorter than an hour")

    return SeatConfig(
        frames_path=frames_path,
        captures_path=captures_path,
        budget_seconds=budget,
        display=_text(raw, "display", DEFAULT_DISPLAY),
        x_user=_text(raw, "x_user", DEFAULT_X_USER),
    )


def _text(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _path(raw: dict[str, Any], section: str, default: Path, base_dir: Path) -> Path:
    """``<section>.path``, resolved against the config file's directory."""
    section_raw = raw.get(section, {})
    if section_raw is None:
        section_raw = {}
    if not isinstance(section_raw, dict):
        raise ConfigError(f"{section} must be a mapping")
    unknown = sorted(str(key) for key in section_raw if key != "path")
    if unknown:
        raise ConfigError(f"Unknown seat config key: {section}.{unknown[0]}")
    value = section_raw.get("path", default)
    if not isinstance(value, (str, Path)) or not str(value):
        raise ConfigError(f"{section}.path must be a non-empty string")
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
