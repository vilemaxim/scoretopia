"""Seat commands: one seat action each, reported as one line of JSON.

A command runs an action through the seat seam and turns what happened into a
``SeatResult``: a line for whoever reads the scheduler's log, and an exit code
the scheduler can act on (ADR 003). Results carry outcomes, error codes, and a
saved frame's file name; never a player's name.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from scoretopia.seat import actions, screen
from scoretopia.seat.seam import Seat


class Outcome(Enum):
    """How a seat command ended. The value is the process's exit code."""

    SUCCEEDED = 0
    FAILED = 1
    NOTHING_TO_DO = 10
    UNKNOWN_SCREEN = 20


@dataclass(frozen=True)
class SeatResult:
    action: str
    outcome: Outcome
    #: Why it failed: a fixed word, such as a timeout's name.
    code: str | None = None
    #: File name of the frame saved from an unknown screen.
    frame: str | None = None

    @property
    def exit_code(self) -> int:
        return self.outcome.value

    def line(self) -> str:
        fields: dict[str, str] = {
            "action": self.action,
            "outcome": self.outcome.name.lower(),
        }
        if self.code is not None:
            fields["code"] = self.code
        if self.frame is not None:
            fields["frame"] = self.frame
        return json.dumps(fields)


def failure(action: str, error: Exception) -> SeatResult:
    """The result for an error that stopped an action short of its end."""
    return SeatResult(action, Outcome.FAILED, code=_failure_code(error))


def _failure_code(error: Exception) -> str:
    if isinstance(error, screen.Budget):
        return "budget"
    if isinstance(error, screen.Stop):
        return str(error)
    if isinstance(error, subprocess.CalledProcessError):
        return screen.failed_code(error)
    if isinstance(error, FileNotFoundError):
        return "tool_missing"
    return "os_error"


#: The errors a seat action can end on, other than a bug.
SEAT_ERRORS = (screen.Stop, screen.Budget, subprocess.CalledProcessError, OSError)


def save_unknown_frame(seat: Seat, frames_dir: Path, action: str) -> str:
    """Save the screen as it is now; returns the new file's name.

    The file is readable by its owner only: a frame can show player names.
    """
    frames_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    taken = datetime.fromtimestamp(seat.clock.now(), UTC)
    name = f"unknown-{taken:%Y%m%dT%H%M%SZ}-{action}.png"
    screen.save(seat.frames.grab(), os.fspath(frames_dir / name))
    return name


def main_menu(seat: Seat, frames_dir: Path) -> SeatResult:
    """Return the client to the main menu."""
    action = "main-menu"
    try:
        try:
            moved = actions.to_main_menu(seat)
        except screen.Stop as stop:
            if str(stop) != "unknown_screen":
                raise
            frame = save_unknown_frame(seat, frames_dir, action)
            return SeatResult(action, Outcome.UNKNOWN_SCREEN, frame=frame)
    except SEAT_ERRORS as error:
        return failure(action, error)
    return SeatResult(action, Outcome.SUCCEEDED if moved else Outcome.NOTHING_TO_DO)
