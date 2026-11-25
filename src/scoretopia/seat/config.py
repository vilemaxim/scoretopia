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
DEFAULT_BUDGET_SECONDS = 3300
DEFAULT_DISPLAY = ":99"
DEFAULT_X_USER = "steam"

_KEYS = frozenset({"frames", "budget_seconds", "display", "x_user"})


@dataclass(frozen=True)
class SeatConfig:
    #: Where the frame of an unknown screen is saved.
    frames_path: Path
    #: Seconds a command may spend before it stops itself.
    budget_seconds: int = DEFAULT_BUDGET_SECONDS
    #: The X display the game client runs on.
    display: str = DEFAULT_DISPLAY
    #: The user X commands run as when the command itself runs as root.
    x_user: str = DEFAULT_X_USER


def load_seat_config(path: Path | None = None) -> SeatConfig:
    """Load and validate seat control's YAML configuration.

    A relative ``frames.path`` resolves against the directory containing the
    file.
    """
    config_path = Path(path) if path is not None else DEFAULT_SEAT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigError(f"Seat config file missing: {config_path}")
    raw = yaml.safe_load(config_path.read_text())
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("Seat config root must be a mapping")

    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise ConfigError(f"Unknown seat config key: {unknown[0]}")

    frames_raw = raw.get("frames") or {}
    if not isinstance(frames_raw, dict):
        raise ConfigError("frames must be a mapping")
    unknown = sorted(set(frames_raw) - {"path"})
    if unknown:
        raise ConfigError(f"Unknown seat config key: frames.{unknown[0]}")
    frames_value = frames_raw.get("path", DEFAULT_FRAMES_PATH)
    if not isinstance(frames_value, (str, Path)) or not str(frames_value):
        raise ConfigError("frames.path must be a non-empty string")
    frames_path = Path(frames_value)
    if not frames_path.is_absolute():
        frames_path = config_path.parent / frames_path

    budget = raw.get("budget_seconds", DEFAULT_BUDGET_SECONDS)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ConfigError("budget_seconds must be a positive integer")
    if budget >= 3600:
        raise ConfigError("budget_seconds must be shorter than an hour")

    return SeatConfig(
        frames_path=frames_path.resolve(),
        budget_seconds=budget,
        display=_text(raw, "display", DEFAULT_DISPLAY),
        x_user=_text(raw, "x_user", DEFAULT_X_USER),
    )


def _text(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value
