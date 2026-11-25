"""Accepting invitations from known players.

The seat walks the Ongoing tab and opens each card's panel once. A panel that
is an invitation has its names read. The seat accepts when the people shown
with it are all known players, and otherwise closes the panel by BACK and
leaves the invitation pending. It never declines.

An invitation shows every player of the game, the seat among them: those who
have joined carry a tick, the rest a question mark. With two players the other
one is the sender. With three or more the sender cannot be told apart from
another invited player who has joined, so the rule is the same for both: every
human player shown, other than the seat, must be a known player. The seat
knows its own name from its configuration; without it nothing is accepted.

Names read from the screen are matched to the orders' known players with the
folding capture ingest uses for the glyphs OCR confuses. No match, or more
than one, is not known.

Nothing here prints, logs, or returns a name: results are counts.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from scoretopia.domain.matching import is_bot_name
from scoretopia.screenshot.name_matching import match_known_name, ocr_name_key
from scoretopia.seat import screen
from scoretopia.seat.actions import ScreenStop, press, to_list, wait_for
from scoretopia.seat.capture import SETUP, walk
from scoretopia.seat.panels import (
    ACCEPT_X,
    INVITATION,
    Panel,
    close_panel,
    joined_lobby,
    open_card,
    read_panel,
)
from scoretopia.seat.seam import Seat

#: Reads the player names an invitation's panel shows, AI opponents included.
NameReader = Callable[[Image.Image], Sequence[str]]

#: Seconds the tribe screen may take to show after ACCEPT, and the joined
#: lobby's panel after PICK.
LOAD_WAIT = 60.0
#: Seconds one invitation is given: the two loads, and three shorter waits.
ACCEPT_BUDGET = 2 * LOAD_WAIT + 120
#: The first tribe, the only one seen picked, on each of the two grids PICK
#: YOUR TRIBE has been seen with after ACCEPT: the accept side's higher rows,
#: and the rows creating a game shows.
ACCEPT_GRID_TRIBE = (screen.ACCEPT_TRIBE_X[0], screen.ACCEPT_TRIBE_Y[0])
CREATE_GRID_TRIBE = (screen.TRIBE_X[0], screen.TRIBE_Y[0])
#: PICK on a tribe's panel.
PICK = (769, 579)


@dataclass
class AcceptCounts:
    accepted: int = 0
    #: Invitations closed and left pending.
    unanswered: int = 0
    #: Of those, the ones left because the reader of names failed.
    unread: int = 0
    #: True when the walk reached the list's end.
    list_end: bool = False
    #: True when the walk stopped because too little time was left.
    budget_reached: bool = False


def all_known(
    shown: Sequence[str], known_players: Sequence[str], seat_name: str | None
) -> bool:
    """Whether an invitation showing these names is one to accept.

    True when the seat is shown once, at least one other human player is
    shown, and each of those matches exactly one known player.
    """
    if not seat_name:
        return False
    seat_key = ocr_name_key(seat_name)
    humans = [name for name in shown if not is_bot_name(name)]
    others = [name for name in humans if ocr_name_key(name) != seat_key]
    if len(humans) - len(others) != 1 or not others:
        return False
    return all(match_known_name(name, known_players) is not None for name in others)


def ocr_names(frame: Image.Image) -> list[str]:
    """The names on an invitation's panel, read with the repository's OCR."""
    from scoretopia.screenshot.extract import extract_screenshot
    from scoretopia.screenshot.models import GameBasicsExtraction

    # The directory and the frame in it are the owner's only, and are removed.
    with tempfile.TemporaryDirectory(prefix="scoretopia-seat-") as directory:
        path = Path(directory) / "invitation.png"
        frame.save(path)
        result = extract_screenshot(path)
    if not isinstance(result, GameBasicsExtraction):
        return []
    return [player.name for player in result.players]


def _accept(seat: Seat, panel: Panel) -> Image.Image:
    """ACCEPT, the first tribe, and PICK; the joined lobby's panel it leads to."""
    press(seat, (ACCEPT_X, panel.y))
    tribes = wait_for(seat, screen.tribe_screen, "accept_tribe_screen", LOAD_WAIT)
    # tribe_screen() holds on both grids; accept_tribe_screen() on one.
    on_accept_grid = screen.accept_tribe_screen(tribes)
    press(seat, ACCEPT_GRID_TRIBE if on_accept_grid else CREATE_GRID_TRIBE)
    wait_for(seat, screen.pick_ready, "pick")
    press(seat, PICK)
    return wait_for(seat, joined_lobby, "invited_lobby", LOAD_WAIT)


def accept_invitations(
    seat: Seat,
    known_players: Sequence[str],
    seat_name: str | None,
    read_names: NameReader,
    counts: AcceptCounts,
) -> None:
    """Answer every invitation on the Ongoing tab, from wherever the client is.

    ``counts`` is filled in as the walk goes, so it is right whatever stops
    it. Raises ``Budget`` when too little time is left for another invitation.
    """
    to_list(seat)

    def visit(row: tuple[int, int], key: str, mask: str) -> None:
        frame, panel = open_card(seat, row)
        if panel.kind != INVITATION:
            close_panel(seat, panel)
            return
        try:
            shown = read_names(frame)
        except Exception:
            # Whatever the reader could not do, an unread panel names no one:
            # the invitation is left, and the count says a read failed.
            shown = ()
            counts.unread += 1
        if not all_known(shown, known_players, seat_name):
            close_panel(seat, panel)
            counts.unanswered += 1
            return
        if panel.y is None:
            raise ScreenStop("invitation_without_accept", frame)
        lobby = _accept(seat, panel)
        # Accepted once the lobby shows it, whatever stops the way back.
        counts.accepted += 1
        close_panel(seat, read_panel(lobby))

    counts.list_end = walk(seat, SETUP, "ongoing", visit, ACCEPT_BUDGET)
