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
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from PIL import Image

from scoretopia.seat import actions, capture, screen
from scoretopia.seat.seam import Seat

#: The action name of ``scoretopia seat main-menu``.
MAIN_MENU = "main-menu"
#: The action name of ``scoretopia seat capture-finished``.
CAPTURE_FINISHED = "capture-finished"


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
    #: What the action counted: whole numbers and yes-or-no facts, never names.
    counts: Mapping[str, int | bool] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return self.outcome.value

    def line(self) -> str:
        fields: dict[str, str | int | bool] = {
            "action": self.action,
            "outcome": self.outcome.name.lower(),
            **self.counts,
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


def save_unknown_frame(
    frame: Image.Image, frames_dir: Path, action: str, taken_at: float
) -> str:
    """Save the frame of a screen no test recognised; returns the file's name.

    The file is readable by its owner only: a frame can show player names.
    """
    frames_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    taken = datetime.fromtimestamp(taken_at, UTC)
    stem = f"unknown-{taken:%Y%m%dT%H%M%SZ}-{action}"
    name, copy = f"{stem}.png", 1
    while (frames_dir / name).exists():
        copy += 1
        name = f"{stem}-{copy}.png"
    screen.save(frame, os.fspath(frames_dir / name))
    return name


def stopped_on_screen(
    seat: Seat, stop: actions.ScreenStop, frames_dir: Path, action: str
) -> SeatResult:
    """The result for an action that met a screen it did not expect.

    Saves the frame the action stopped on. ``code`` says which expectation
    failed, when it was a particular one.
    """
    frame = save_unknown_frame(stop.frame, frames_dir, action, seat.clock.now())
    code = None if isinstance(stop, actions.UnknownScreen) else str(stop)
    return SeatResult(action, Outcome.UNKNOWN_SCREEN, code=code, frame=frame)


def main_menu(seat: Seat, frames_dir: Path) -> SeatResult:
    """Return the client to the main menu."""
    action = MAIN_MENU
    try:
        try:
            moved = actions.to_main_menu(seat)
        except actions.ScreenStop as stop:
            return stopped_on_screen(seat, stop, frames_dir, action)
    except SEAT_ERRORS as error:
        return failure(action, error)
    return SeatResult(action, Outcome.SUCCEEDED if moved else Outcome.NOTHING_TO_DO)


def _back_to_main_menu(seat: Seat) -> None:
    """Try once to leave the client on the main menu after a stop."""
    try:
        actions.to_main_menu(seat)
    except SEAT_ERRORS:
        pass


def capture_finished(seat: Seat, captures_root: Path, frames_dir: Path) -> SeatResult:
    """Capture every finished game not yet captured, then return to the menu.

    Succeeds when it captured at least one game; a list with nothing new is
    nothing to do. A capture that stops half way leaves a directory with no
    manifest, which ingest ignores and the next run completes.
    """
    action = CAPTURE_FINISHED
    counts = capture.CaptureCounts()
    try:
        try:
            try:
                capture.capture_finished(seat, captures_root, counts)
            except screen.Budget:
                counts.budget_reached = True
            actions.to_main_menu(seat)
        except actions.ScreenStop as stop:
            result = stopped_on_screen(seat, stop, frames_dir, action)
            _back_to_main_menu(seat)
            return replace(result, counts=asdict(counts))
    except SEAT_ERRORS as error:
        return replace(failure(action, error), counts=asdict(counts))
    if counts.captured:
        outcome, code = Outcome.SUCCEEDED, None
    elif counts.budget_reached:
        outcome, code = Outcome.FAILED, "budget"
    else:
        outcome, code = Outcome.NOTHING_TO_DO, None
    return SeatResult(action, outcome, code=code, counts=asdict(counts))
