"""Capturing games: one capture per game per kind, from the Multiplayer list.

A finished game: for each row of the Replays tab not captured before, the seat
opens the row's panel, saves it, saves its Game Info, opens the replay, waits
for it to play to its last turn, presses Finish Game, saves the end screen, and
presses DONE. The screens go to ``<root>/result/<key>/``.

A game's setup: for each card of the Ongoing tab not captured before, the seat
opens the card's panel, saves it, saves its Game Info, and closes both. The
screens go to ``<root>/setup/<key>/``. A card whose game has not started, a
lobby or an invitation, is closed and not recorded, so it is captured once the
game starts.

Each directory gets a ``manifest.json`` written last, which is the layout
capture ingest reads (``domain/captures.py``): a directory with no manifest is
a capture that did not finish, and ingest passes over it.

Seat control keeps its own record of what it has captured, ``record.json`` in
the captures root, readable by its owner only. The seat may resign only from
a game whose setup the record holds: ``CaptureRecord.knows()`` answers that for
a card. A row or card is known by a mask of its white text, matched with a
tolerance because the list renders rows at sub-pixel heights; the screen
layer's ``key_of()`` and ``match()`` carry the measurements. The record's
format is the one written by the capture routine this module replaces, so
games already captured are not captured again.

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
SETUP = "setup"
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
#: Seconds a setup capture must have left to begin: it has five waits, each
#: of the seat's wait at worst (20 s by default), and no replay to watch. Not
#: measured on the live client.
SETUP_BUDGET = 150.0
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
#: More info on an Ongoing card's panel, the last of its icons.
CARD_INFO_X = 749
#: A started game's OPEN begins right of this x. An invitation's ACCEPT and a
#: lobby's START GAME, which share OPEN's row, are blue here; see the screen
#: layer's notes on ``invite_panel()``.
WIDER_THAN_OPEN_X = 705
#: A joined lobby's BACK is a short button centred at x 640, blue at neither
#: of these. Any button that is blue at one of them is something else.
WIDER_THAN_BACK_X = (575, WIDER_THAN_OPEN_X)


@dataclass
class CaptureCounts:
    captured: int = 0
    #: Rows passed over because the record already holds them.
    skipped: int = 0
    #: True when the sweep reached the list's end.
    list_end: bool = False
    #: True when the sweep stopped because too little time was left.
    budget_reached: bool = False


@dataclass
class SetupCounts(CaptureCounts):
    #: Cards passed over because their game has not started: lobbies and
    #: invitations. They are not recorded.
    not_started: int = 0


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
        """Whether a row with these mask variants was captured before.

        ``variants`` is the screen layer's ``mask_variants()`` of the row, read
        with the same kind. ``knows(SETUP, ...)`` of an Ongoing card is the
        question "is this game's setup captured?".
        """
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


def _save_info(seat: Seat, info_x: int, top: int, path: Path) -> None:
    """Open a panel's Game Info, save it, and close it back to the panel."""
    press(seat, (info_x, top + INFO_BELOW_TOP))
    info = wait_for(seat, lambda i: _info_back(i, top) is not None, "info")
    _save(info, path)
    press(seat, (640, _info_back(info, top)))
    wait_for(
        seat, lambda i: _has_panel(i) and _info_back(i, top) is None, "info_closed"
    )


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
    open_y = _button_y(panel, PANEL_OPEN_X, top)
    if open_y is None:
        raise ScreenStop("replay_panel_without_open", panel)

    directory = _capture_dir(root, RESULT, key)
    captured_at = _timestamp(seat)
    _save(panel, directory / "setup.png")

    _save_info(seat, REPLAY_INFO_X, top, directory / "info.png")

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


def open_record(root: Path) -> CaptureRecord:
    """The record of the captures root, which is made when it is not there."""
    if not root.is_dir():
        root.mkdir(mode=DIR_MODE, parents=True)
        os.chmod(root, DIR_MODE)
    return CaptureRecord(root / RECORD_NAME)


def capture_finished(seat: Seat, root: Path, counts: CaptureCounts) -> None:
    """Capture every finished game not yet captured, from wherever the client is.

    ``counts`` is filled in as the sweep goes, so it is right whatever stops
    it. Raises ``Budget`` when too little time is left for another capture.
    The record is read before anything is clicked.
    """
    record = open_record(root)
    to_list(seat)

    def capture_one(row: tuple[int, int], key: str, mask: str) -> None:
        capture_replay(seat, root, record, counts, row, key, mask)

    sweep(seat, RESULT, "replays", record, counts, capture_one, CAPTURE_BUDGET)


# ------------------------------------------------------------ one setup


def _button_y(panel: Image.Image, x: int, top: int) -> int | None:
    """The y of a blue button at ``x`` in the row of a panel's bottom buttons."""
    return screen.find_blue(panel, x, top + 250, top + 420)


def _open_y_or_close(seat: Seat, panel: Image.Image, top: int) -> int | None:
    """The y of OPEN on a started game's panel; None once any other is closed.

    An invitation is left by its BACK, which neither accepts nor declines. A
    joined lobby has no OPEN, and its one button is a blue BACK at the centre.
    A panel with a button wider than OPEN or than that BACK, such as a lobby's
    START GAME, is none of these: nothing on it is pressed, and the action
    stops.
    """
    if screen.invite_panel(panel):
        press(seat, screen.INVITE_BACK)
        wait_for(seat, screen.mp_list, "invite_back")
        return None
    open_y = _button_y(panel, PANEL_OPEN_X, top)
    if open_y is None:
        back_y = _button_y(panel, 640, top)
        if back_y is None:
            raise ScreenStop("lobby_without_back", panel)
        if any(screen.blue(screen.px(panel, x, back_y)) for x in WIDER_THAN_BACK_X):
            raise ScreenStop("lobby_without_back", panel)
        press(seat, (640, back_y))
        wait_for(seat, screen.mp_list, "panel_closed")
        return None
    if screen.blue(screen.px(panel, WIDER_THAN_OPEN_X, open_y)):
        raise ScreenStop("card_panel_without_open", panel)
    return open_y


def capture_card(
    seat: Seat,
    root: Path,
    record: CaptureRecord,
    counts: SetupCounts,
    row: tuple[int, int],
    key: str,
    mask: str,
) -> None:
    """Capture one game's setup from its card on the Ongoing tab."""
    press(seat, (640, (row[0] + row[1]) // 2))
    panel = wait_for(seat, _has_panel, "card_panel")
    top = screen.panel_top(panel)
    open_y = _open_y_or_close(seat, panel, top)
    if open_y is None:
        counts.not_started += 1
        return

    directory = _capture_dir(root, SETUP, key)
    captured_at = _timestamp(seat)
    _save(panel, directory / "card.png")
    _save_info(seat, CARD_INFO_X, top, directory / "info.png")
    # The panel's BACK shares OPEN's row.
    press(seat, (PANEL_BACK_X, open_y))
    wait_for(seat, screen.mp_list, "panel_closed")

    write_json(
        directory / MANIFEST_NAME,
        {
            "kind": SETUP,
            "captured_at": captured_at,
            "start": "unknown",
            "screens": {"card": "card.png", "info": "info.png"},
        },
        FILE_MODE,
    )
    record.add(SETUP, key, mask)
    counts.captured += 1


def capture_setup(seat: Seat, root: Path, counts: SetupCounts) -> None:
    """Capture the setup of every started game not yet captured.

    As ``capture_finished()``, over the Ongoing tab.
    """
    record = open_record(root)
    to_list(seat)

    def capture_one(row: tuple[int, int], key: str, mask: str) -> None:
        capture_card(seat, root, record, counts, row, key, mask)

    sweep(seat, SETUP, "ongoing", record, counts, capture_one, SETUP_BUDGET)
