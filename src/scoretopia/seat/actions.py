"""Seat actions: what the seat does to the game client, through the seat seam.

Every function here takes a ``Seat`` and reaches the client only through its
frame source, input sink, and clock. The screen tests come from the screen
layer; its own waits and moves, which call the tools directly, are not used.

Two faults of the live client shape how input is sent. It can drop the first
click after the screen has been idle, and a frame can still show the screen
from before the last input. ``press()`` therefore moves the pointer onto its
target before clicking and parks it off the target afterwards, and a wait
accepts a screen only when two frames in a row show it.

No function here declines an invitation, deletes or starts a game, or changes
the profile or settings.
"""

from __future__ import annotations

from collections.abc import Callable

from PIL import Image

from scoretopia.seat import screen
from scoretopia.seat.screen import Budget, Stop
from scoretopia.seat.seam import Seat

ScreenTest = Callable[[Image.Image], object]


class UnknownScreen(Stop):
    """No known screen showed and Escape did not leave it; carries the frame."""

    def __init__(self, frame: Image.Image) -> None:
        super().__init__("unknown_screen")
        self.frame = frame


#: Where the pointer rests while a frame is read: empty background on every
#: screen these actions cross.
PARK = screen.PARK
#: Seconds the pointer rests on a target before the click.
HOVER = 0.5
#: Seconds between two looks at the screen during a wait.
POLL = 1.0
#: The back arrow of the Multiplayer list and of Settings.
BACK_ARROW = (31, 31)
#: The back arrow of GAME SETUP and of both PICK YOUR TRIBE screens.
SETUP_BACK = screen.TRIBE_BACK


def park(seat: Seat) -> None:
    """Rest the pointer where it changes nothing a frame is read for."""
    seat.inputs.move(*PARK)
    seat.clock.sleep(seat.settle)


def check_budget(seat: Seat) -> None:
    """Raise ``Budget`` when the seat's deadline has passed."""
    if seat.deadline is not None and seat.clock.now() > seat.deadline:
        raise Budget()


def press(seat: Seat, point: tuple[int, int]) -> None:
    """Click a point: pointer onto it first, and off it before any frame.

    Raises ``Budget``, with nothing clicked, when the deadline has passed.
    """
    check_budget(seat)
    seat.inputs.move(*point)
    seat.clock.sleep(HOVER)
    seat.inputs.click(*point)
    park(seat)


def poll_for(seat: Seat, test: ScreenTest, timeout: float) -> Image.Image | None:
    """The first frame of two in a row that pass ``test``; None on timeout.

    Raises ``Budget`` when the seat's deadline passes while the screen is
    still not showing. A screen seen once is given its second frame.
    """
    end = seat.clock.now() + timeout
    held = False
    while True:
        frame = seat.frames.grab()
        if test(frame):
            if held:
                return frame
            held = True
        else:
            held = False
        if not held:
            check_budget(seat)
        if seat.clock.now() > end:
            return None
        seat.clock.sleep(POLL)


def wait_for(
    seat: Seat, test: ScreenTest, what: str, timeout: float | None = None
) -> Image.Image:
    """As ``poll_for()``, for at least the seat's wait; ``Stop`` on timeout."""
    frame = poll_for(seat, test, max(timeout or seat.wait, seat.wait))
    if frame is None:
        raise Stop("timeout_" + what)
    return frame


def _known(frame: Image.Image) -> bool:
    return bool(
        screen.main_menu(frame)
        or screen.mp_list(frame)
        or screen.invite_panel(frame)
        or screen.settings_screen(frame)
        or screen.setup_screen(frame)
        or screen.tribe_screen(frame)
    )


def look(seat: Seat) -> Image.Image:
    """The screen once it is one recover() knows, or as it is after the wait."""
    return poll_for(seat, _known, seat.wait) or seat.frames.grab()


def recover(seat: Seat, frame: Image.Image | None = None) -> Image.Image:
    """To the main menu or the Multiplayer list with no panel open.

    ``frame`` is the screen as the caller last saw it, when it has looked.

    Each known screen is left by its own way back: an invitation's panel by
    its BACK, which neither accepts nor declines; Settings by its back arrow;
    PICK YOUR TRIBE and GAME SETUP by theirs. Any other screen is tried with
    Escape, at most ``seat.escapes`` times, and then stops the action with
    ``UnknownScreen``. Escape is never pressed on the list, where it opens
    Settings.
    """
    if frame is None:
        frame = look(seat)
    for attempt in range(seat.escapes + 1):
        if screen.main_menu(frame) or screen.mp_list(frame):
            return frame
        if screen.invite_panel(frame):
            press(seat, screen.INVITE_BACK)
            return wait_for(seat, screen.mp_list, "invite_back")
        if screen.settings_screen(frame):
            press(seat, BACK_ARROW)
            return wait_for(seat, screen.main_menu, "main_menu")
        if screen.tribe_screen(frame):
            press(seat, SETUP_BACK)
            frame = wait_for(seat, screen.setup_screen, "tribe_back")
        if screen.setup_screen(frame):
            press(seat, SETUP_BACK)
            return wait_for(seat, screen.mp_list, "setup_back")
        if attempt == seat.escapes:
            break
        seat.inputs.key("Escape")
        seat.clock.sleep(seat.settle)
        frame = look(seat)
    raise UnknownScreen(frame)


def _main_menu_or_reopened_tribe(frame: Image.Image) -> bool:
    return bool(screen.main_menu(frame) or screen.list_back_tribe(frame))


def _main_menu_or_list(frame: Image.Image) -> bool:
    return bool(screen.main_menu(frame) or screen.mp_list(frame))


def to_main_menu(seat: Seat) -> bool:
    """Return the client to the main menu; False when it was there already.

    After an accepted invitation the list's back arrow can reopen the accept
    side's PICK YOUR TRIBE. Its own back arrow is clicked, then the list's
    again, at most ``screen.LIST_BACK_HOPS`` times.
    """
    park(seat)
    frame = look(seat)
    if screen.main_menu(frame):
        return False
    frame = recover(seat, frame)
    hops = 0
    while screen.mp_list(frame):
        press(seat, BACK_ARROW)
        frame = wait_for(seat, _main_menu_or_reopened_tribe, "main_menu")
        if screen.main_menu(frame):
            break
        hops += 1
        if hops > screen.LIST_BACK_HOPS:
            raise Stop("list_back_hops")
        press(seat, SETUP_BACK)
        frame = wait_for(seat, _main_menu_or_list, "list_back_tribe")
    return True
