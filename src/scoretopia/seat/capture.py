"""Capturing finished games: the Replays tab, one capture per game.

For each row of the Replays tab not captured before, the seat opens the row's
panel, saves it, saves its Game Info, opens the replay, waits for it to play to
its last turn, presses Finish Game, saves the end screen, and presses DONE.
The screens go to ``<root>/result/<key>/`` with a ``manifest.json`` written
last, which is the layout capture ingest reads (``domain/captures.py``): a
directory with no manifest is a capture that did not finish, and ingest passes
over it.

Seat control keeps its own record of what it has captured, ``record.json`` in
the captures root, readable by its owner only. A row is known by a mask of its
white text, matched with a tolerance because the list renders rows at
sub-pixel heights; the screen layer's ``key_of()`` and ``match()`` carry the
measurements. The record's format is the one written by the capture routine
this module replaces, so games already captured are not captured again.

Nothing here reads a name: rows are told apart by their pixels, and directory
names are hashes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from scoretopia.seat import screen
from scoretopia.seat.actions import (
    ScreenStop,
    check_budget,
    go_tab,
    poll_for,
    press,
    require_budget,
    to_list,
    wait_for,
    wheel,
)
from scoretopia.seat.screen import Stop
from scoretopia.seat.seam import Seat

RESULT = "result"
RECORD_NAME = "record.json"
MANIFEST_NAME = "manifest.json"
#: The record's lists: per kind, exact keys and the masks rows are matched by.
RECORD_KEYS = ("setup", "result", "setup_masks", "result_masks")

#: Capture directories are searched and read by the recording user through
#: their group; the setgid bit keeps that group on what is made inside.
DIR_MODE = 0o2750
FILE_MODE = 0o640
RECORD_MODE = 0o600

#: Seconds between looks at a replay while it plays.
REPLAY_POLL = 5.0
#: Seconds a replay may play before the seat gives up on it. An eight-turn
#: replay took about two minutes.
FINISH_WAIT = 600.0
#: Seconds the end screen may take to show after Finish Game.
END_WAIT = 30.0
#: Seconds a wheel turn may take to show; a list that has not moved by then
#: is at its end.
SCROLL_WAIT = 10.0
#: Seconds a tab may take to show its first row.
LIST_WAIT = 30.0
#: The worst case of one capture: the replay, plus the waits around it.
CAPTURE_BUDGET = FINISH_WAIT + 180
#: The span the screen layer reads rows in: a row touching either edge is cut.
LIST_TOP, LIST_BOTTOM = 130, 625
#: Notches per wheel turn.
NOTCHES = 3

#: A replay's bottom-right control: Exit while it plays, Finish Game at its end.
REPLAY_CONTROL = (765, 665)
#: The x of a panel's BACK and OPEN, which share a row.
PANEL_BACK_X, PANEL_OPEN_X = 516, 760
#: More info on a replay's panel: its x, and how far below the panel's top.
REPLAY_INFO_X, INFO_BELOW_TOP = 676, 73


@dataclass
class CaptureCounts:
    captured: int = 0
    #: Rows passed over because the record already holds them.
    skipped: int = 0
    #: True when the sweep reached the list's end.
    list_end: bool = False
    #: True when the sweep stopped because too little time was left.
    budget_reached: bool = False


class CaptureRecord:
    """What seat control has captured, kept in ``record.json``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError) as error:
            raise Stop("record_unreadable") from error
        if not isinstance(raw, dict) or not all(
            isinstance(raw.get(key, []), list)
            and all(isinstance(item, str) for item in raw.get(key, []))
            for key in RECORD_KEYS
        ):
            raise Stop("record_unreadable")
        self._data = raw
        for key in RECORD_KEYS:
            self._data.setdefault(key, [])

    def knows(self, kind: str, variants: list[str]) -> bool:
        """Whether a row with these mask variants was captured before."""
        return screen.match(variants, self._data[kind + "_masks"]) is not None

    def add(self, kind: str, key: str, mask: str) -> None:
        self._data[kind].append(key)
        self._data[kind + "_masks"].append(mask)
        write_json(self._path, self._data, RECORD_MODE)


def write_json(path: Path, data: object, mode: int) -> None:
    """Write a JSON file in one step, created with ``mode``."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _capture_dir(root: Path, kind: str, key: str) -> Path:
    directory = root / kind / key
    directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    for made in (directory.parent, directory):
        os.chmod(made, DIR_MODE)
    return directory


def _save(frame: Image.Image, path: Path) -> None:
    screen.save(frame, os.fspath(path), mode=FILE_MODE)


def _timestamp(seat: Seat) -> str:
    return datetime.fromtimestamp(seat.clock.now(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ the list


def _signature(frame: Image.Image, kind: str) -> tuple[list, list]:
    """What a wheel turn changes: where the rows are, and which they are."""
    rows = screen.bands(frame)
    return rows, [screen.key_of(frame, row, kind) for row in rows]


def _wheel_moves_list(seat: Seat, frame: Image.Image, kind: str, notches: int):
    """Turn the wheel; the list's frame once it has moved, or None."""
    before = _signature(frame, kind)
    wheel(seat, notches)
    return poll_for(
        seat,
        lambda i: screen.mp_list(i) and _signature(i, kind) != before,
        SCROLL_WAIT,
    )


def to_top(seat: Seat, kind: str) -> Image.Image:
    """Wheel the list up until a turn moves nothing; the frame at its top.

    The list keeps its scroll position within a client session, and can open
    at its end with its top row under the tab bar.
    """
    frame = wait_for(seat, screen.mp_list, "list")
    for _ in range(screen.PAGE_CAP):
        moved = _wheel_moves_list(seat, frame, kind, -NOTCHES)
        if moved is None:
            break
        frame = moved
    # The last frame of a moving list can be mid-scroll; read a settled one.
    return wait_for(seat, screen.mp_list, "list_top")


def read_tab(seat: Seat, tab: str) -> Image.Image:
    """The tab's frame, given time to show its rows; an empty list is fine."""
    go_tab(seat, tab)
    shown = poll_for(seat, lambda i: screen.mp_list(i) and screen.bands(i), LIST_WAIT)
    return shown if shown is not None else wait_for(seat, screen.mp_list, "list")


CaptureOne = Callable[[tuple[int, int], str, str], None]


def sweep(
    seat: Seat,
    kind: str,
    tab: str,
    record: CaptureRecord,
    counts: CaptureCounts,
    capture_one: CaptureOne,
    capture_budget: float,
) -> None:
    """Read a tab from its top to its end, capturing each row not yet captured.

    A page is read again after each capture, since a capture leaves and
    returns to the list. Rows seen earlier in the sweep are passed over by
    their masks. A row cut by the tab bar or by the list's bottom edge is not
    read, since its mask would be that of another game: it was whole on the
    page before, or will be on the next. At the list's end the last row is
    whole.
    """
    seen: list[str] = []
    at_top = False
    for page in range(screen.PAGE_CAP + 1):
        while True:
            frame = read_tab(seat, tab)
            if not at_top:
                frame = to_top(seat, kind)
                at_top = True
            todo = None
            for row in screen.bands(frame):
                if row[0] <= LIST_TOP or row[1] >= LIST_BOTTOM:
                    continue
                variants = screen.mask_variants(frame, row, kind)
                if not variants or screen.match(variants, seen) is not None:
                    continue
                mask = screen.mask_of(frame, row, kind)
                if mask is None:
                    # Text shows only a pixel or two off the row's box: the
                    # row is still settling, and a later page reads it.
                    continue
                seen.append(mask)
                if record.knows(kind, variants):
                    counts.skipped += 1
                    continue
                todo = (row, screen.key_of(frame, row, kind), mask)
                break
            if todo is None:
                break
            require_budget(seat, capture_budget)
            capture_one(*todo)
        if page == screen.PAGE_CAP:
            return
        require_budget(seat, SCROLL_WAIT + 30)
        if _wheel_moves_list(seat, frame, kind, NOTCHES) is None:
            counts.list_end = True
            return


# ------------------------------------------------------------ one replay


def _has_panel(frame: Image.Image) -> bool:
    return screen.panel_top(frame) is not None


def _info_back(frame: Image.Image, top: int) -> int | None:
    """The y of Game Info's BACK, which shows only while Game Info is open."""
    return screen.find_blue(frame, 640, top + 200, top + 300)


def _await_finish(seat: Seat) -> None:
    """Watch a replay until Finish Game shows.

    The control is Exit until the last turn. It is pressed here only to leave
    a replay that has not finished within ``FINISH_WAIT``; on the Multiplayer
    list the same point is the Profile icon, so it is never pressed there.
    """
    opened = seat.clock.now()
    playing = False
    while True:
        frame = seat.frames.grab()
        if screen.finish_green(frame):
            return
        if screen.exit_blue(frame):
            playing = True
        elif screen.on_multiplayer(frame) and (
            playing or seat.clock.now() > opened + seat.wait
        ):
            raise ScreenStop("replay_not_open", frame)
        if seat.clock.now() > opened + FINISH_WAIT:
            if screen.exit_blue(frame):
                press(seat, REPLAY_CONTROL)
                wait_for(seat, screen.mp_list, "exit_to_multiplayer")
            raise ScreenStop("finish_timeout", frame)
        check_budget(seat)
        seat.clock.sleep(REPLAY_POLL)


def capture_replay(
    seat: Seat,
    root: Path,
    record: CaptureRecord,
    counts: CaptureCounts,
    row: tuple[int, int],
    key: str,
    mask: str,
) -> None:
    """Capture one finished game from its row on the Replays tab."""
    press(seat, (640, (row[0] + row[1]) // 2))
    panel = wait_for(seat, _has_panel, "replay_panel")
    top = screen.panel_top(panel)
    open_y = screen.find_blue(panel, PANEL_OPEN_X, top + 250, top + 420)
    if open_y is None:
        raise ScreenStop("replay_panel_without_open", panel)

    directory = _capture_dir(root, RESULT, key)
    captured_at = _timestamp(seat)
    _save(panel, directory / "setup.png")

    press(seat, (REPLAY_INFO_X, top + INFO_BELOW_TOP))
    info = wait_for(seat, lambda i: _info_back(i, top) is not None, "info")
    _save(info, directory / "info.png")
    press(seat, (640, _info_back(info, top)))
    wait_for(
        seat, lambda i: _has_panel(i) and _info_back(i, top) is None, "info_closed"
    )

    press(seat, (PANEL_OPEN_X, open_y))
    _await_finish(seat)
    press(seat, REPLAY_CONTROL)
    end = poll_for(seat, screen.game_over, END_WAIT)
    if end is None:
        raise ScreenStop("no_victory_screen", seat.frames.grab())
    _save(end, directory / "victory.png")
    press(seat, screen.done_point(end))
    wait_for(seat, screen.mp_list, "done_to_multiplayer")

    write_json(
        directory / MANIFEST_NAME,
        {
            "kind": RESULT,
            "captured_at": captured_at,
            "start": "unknown",
            "screens": {
                "setup": "setup.png",
                "info": "info.png",
                "victory": "victory.png",
            },
        },
        FILE_MODE,
    )
    record.add(RESULT, key, mask)
    counts.captured += 1


def capture_finished(seat: Seat, root: Path, counts: CaptureCounts) -> None:
    """Capture every finished game not yet captured, from wherever the client is.

    ``counts`` is filled in as the sweep goes, so it is right whatever stops
    it. Raises ``Budget`` when too little time is left for another capture.
    The record is read before anything is clicked.
    """
    if not root.is_dir():
        root.mkdir(mode=DIR_MODE, parents=True)
        os.chmod(root, DIR_MODE)
    record = CaptureRecord(root / RECORD_NAME)
    to_list(seat)

    def capture_one(row: tuple[int, int], key: str, mask: str) -> None:
        capture_replay(seat, root, record, counts, row, key, mask)

    sweep(seat, RESULT, "replays", record, counts, capture_one, CAPTURE_BUDGET)
