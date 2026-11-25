"""A card's panel on the Ongoing tab: which kind it is, and how it is left.

A click on an Ongoing card opens a black panel over the list. What the panel
offers depends on the game:

- a started game: grey BACK bottom left and blue OPEN bottom right;
- an invitation: BACK, a blue ACCEPT wider than OPEN, and a red DECLINE;
- a lobby the seat has joined: one short blue BACK at the centre, and a red
  LEAVE that is never pressed.

Any other panel, such as a lobby the seat could start, is not read as one of
these, and nothing on it is pressed. The measurements are in the screen
layer's notes on ``invite_panel()``.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from scoretopia.seat import screen
from scoretopia.seat.actions import ScreenStop, press, wait_for
from scoretopia.seat.seam import Seat

STARTED = "started"
INVITATION = "invitation"
LOBBY = "lobby"

#: The x of a panel's BACK and OPEN, which share a row.
BACK_X, OPEN_X = 516, 760
#: The x of the centre of an invitation's ACCEPT, and the xs its row is looked
#: for at, left and right of its label: at the centre the label's white text
#: breaks the button's blue.
ACCEPT_X, ACCEPT_EDGE_XS = 750, (723, 712, 790)
#: A started game's OPEN begins right of this x. An invitation's ACCEPT and a
#: lobby's START GAME, which share OPEN's row, are blue here.
WIDER_THAN_OPEN_X = 705
#: A joined lobby's BACK is a short button centred at x 640, blue at neither
#: of these. Any button that is blue at one of them is something else.
WIDER_THAN_BACK_X = (575, WIDER_THAN_OPEN_X)


@dataclass(frozen=True)
class Panel:
    kind: str
    #: The y of the row of the panel's bottom buttons. None on an invitation
    #: whose ACCEPT was not found: it can still be closed, by its BACK.
    y: int | None


def has_panel(frame: Image.Image) -> bool:
    return screen.panel_top(frame) is not None


def button_y(frame: Image.Image, x: int, top: int) -> int | None:
    """The y of a blue button at ``x`` in the row of a panel's bottom buttons."""
    return screen.find_blue(frame, x, top + 250, top + 420)


def read_panel(frame: Image.Image) -> Panel:
    """The kind of the card panel a frame shows.

    Raises ``ScreenStop`` on a panel that is none of the three kinds.
    """
    top = screen.panel_top(frame)
    if top is None:
        raise ScreenStop("no_card_panel", frame)
    if screen.invite_panel(frame):
        found = (button_y(frame, x, top) for x in ACCEPT_EDGE_XS)
        return Panel(INVITATION, next((y for y in found if y is not None), None))
    open_y = button_y(frame, OPEN_X, top)
    if open_y is None:
        back_y = button_y(frame, 640, top)
        if back_y is None or any(
            screen.blue(screen.px(frame, x, back_y)) for x in WIDER_THAN_BACK_X
        ):
            raise ScreenStop("lobby_without_back", frame)
        return Panel(LOBBY, back_y)
    if screen.blue(screen.px(frame, WIDER_THAN_OPEN_X, open_y)):
        raise ScreenStop("card_panel_without_open", frame)
    return Panel(STARTED, open_y)


def open_card(seat: Seat, row: tuple[int, int]) -> tuple[Image.Image, Panel]:
    """Click a card of the Ongoing tab; its panel's frame and kind.

    Raises ``ScreenStop`` when no panel opens, or one of no known kind.
    """
    press(seat, (640, (row[0] + row[1]) // 2))
    frame = wait_for(seat, has_panel, "card_panel")
    return frame, read_panel(frame)


def joined_lobby(frame: Image.Image) -> bool:
    """Whether a frame shows the panel of a lobby the seat has joined."""
    if not screen.on_multiplayer(frame):
        return False
    try:
        return read_panel(frame).kind == LOBBY
    except ScreenStop:
        return False


def close_panel(seat: Seat, panel: Panel) -> None:
    """Leave a card's panel by its BACK, to the list.

    An invitation's BACK neither accepts nor declines it.
    """
    if panel.kind == INVITATION:
        press(seat, screen.INVITE_BACK)
        wait_for(seat, screen.mp_list, "invite_back")
        return
    press(seat, (640 if panel.kind == LOBBY else BACK_X, panel.y))
    wait_for(seat, screen.mp_list, "panel_closed")
