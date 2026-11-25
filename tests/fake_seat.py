"""A scripted fake screen: the seat seam's test double.

``FakeScreen`` is a frame source, an input sink, and a clock in one. It holds a
set of states, each with a frame drawn here in code, the boxes of its buttons,
and where each button or key leads. It logs every input, and it can reproduce
the two faults the live client shows:

- ``stale_frames``: after the screen changes, that many frames still show the
  screen from before the input.
- ``stale_until_pointer_leaves``: after a click changes the screen, frames show
  the old screen until the pointer has moved off the button clicked.
- ``drop_unhovered_clicks``: a click is lost unless an earlier, separate input
  had already moved the pointer onto the button and time has passed since.

``hovering_grabs`` counts the frames taken while the pointer was on a button.

A click inside a box a state lists as forbidden raises ``ForbiddenInput``. A
click that lands on no button is logged with no button name, and is listed by
``misses``.

Frames carry no text at all, so no name, real or invented, appears in them.
Button boxes come from the measurements recorded in the screen layer's
comments. A back arrow's box is the square the screen tests read as the back
button's own, x and y 20 to 42; the real button is larger, so a click the fake
counts as a miss may still land on the live one, never the other way round.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from scoretopia.seat import screen
from scoretopia.seat.seam import Seat

Box = tuple[int, int, int, int]

SIZE = (1280, 720)
SKY = (245, 103, 169)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
TAB_BAND = (150, 50, 100)
MENU_TOP_RIGHT = (197, 138, 201)
AMBER = (240, 170, 40)
MAROON = (60, 20, 30)
BUTTON_BLUE = (0, 122, 204)
CIRCLE_BLUE = (0, 153, 255)
GREY = (204, 204, 204)
RED = (204, 41, 0)
GREEN = (90, 200, 90)

BACK_ARROW: Box = (20, 20, 43, 43)
MENU_MULTIPLAYER: Box = (520, 384, 760, 424)
INVITE_DECLINE: Box = (720, 207, 802, 229)
INVITE_BACK: Box = (477, 480, 555, 515)
INVITE_ACCEPT: Box = (698, 480, 802, 517)
LOBBY_START_GAME: Box = (640, 480, 802, 517)
LOBBY_LEAVE: Box = (477, 480, 555, 515)
SETTINGS_BODY: Box = (0, 60, 1280, 720)
PROFILE_BODY: Box = (0, 60, 1280, 720)
TEAL = (40, 170, 200)

#: The kinds of input no code in seat control may send.
FORBIDDEN_KINDS = ("decline", "delete", "start_game", "profile", "settings")

START_TIME = 1_800_000_000.0


class ForbiddenInput(AssertionError):
    """Seat control sent an input it must never send."""


def _inside(box: Box, x: int, y: int) -> bool:
    return box[0] <= x < box[2] and box[1] <= y < box[3]


@dataclass(frozen=True)
class State:
    name: str
    frame: Image.Image
    #: Button name to its box on the screen.
    buttons: Mapping[str, Box] = field(default_factory=dict)
    #: Forbidden kind (one of FORBIDDEN_KINDS) to the box it covers.
    forbidden: Mapping[str, Box] = field(default_factory=dict)
    #: Button name to the state a click on it leads to.
    on_click: Mapping[str, str] = field(default_factory=dict)
    #: Key name to the state pressing it leads to.
    on_key: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.forbidden) - set(FORBIDDEN_KINDS)
        if unknown:
            raise ValueError(f"not a forbidden kind: {sorted(unknown)}")


class FakeScreen:
    def __init__(
        self,
        states: Mapping[str, State],
        start: str,
        *,
        stale_frames: int = 0,
        stale_until_pointer_leaves: bool = False,
        drop_unhovered_clicks: bool = False,
    ) -> None:
        self._states = dict(states)
        self.state = start
        self._shown = start
        self._stale_frames = stale_frames
        self._stale_until_pointer_leaves = stale_until_pointer_leaves
        self._drop_unhovered_clicks = drop_unhovered_clicks
        self._stale_left = 0
        self._held_by: Box | None = None
        self._hovered: str | None = None
        self._pointer = (0, 0)
        self._time = START_TIME
        #: Every input, in order: ("move", x, y), ("click", x, y, button or
        #: None), ("key", name).
        self.log: list[tuple] = []
        self.dropped_clicks = 0
        self.hovering_grabs = 0
        self._hovered_at = self._time
        self.frames_served = 0

    # ------------------------------------------------------------ the seam

    def seat(self, **settings: float) -> Seat:
        return Seat(frames=self, inputs=self, clock=self, **settings)

    def grab(self) -> Image.Image:
        self.frames_served += 1
        if self._hovered is not None:
            self.hovering_grabs += 1
        if self._held_by is not None:
            return self._states[self._shown].frame.copy()
        if self._stale_left > 0:
            self._stale_left -= 1
            return self._states[self._shown].frame.copy()
        self._shown = self.state
        return self._states[self.state].frame.copy()

    def move(self, x: int, y: int) -> None:
        self.log.append(("move", x, y))
        self._pointer = (x, y)
        self._hovered = self._button_at(x, y)
        self._hovered_at = self._time
        if self._held_by is not None and not _inside(self._held_by, x, y):
            self._held_by = None

    def click(self, x: int, y: int) -> None:
        current = self._states[self.state]
        for kind, box in current.forbidden.items():
            if _inside(box, x, y):
                self.log.append(("click", x, y, kind))
                raise ForbiddenInput(
                    f"forbidden input: {kind} clicked at {(x, y)} on {self.state}"
                )
        button = self._button_at(x, y)
        self.log.append(("click", x, y, button))
        was_hovered = self._hovered == button and self._time > self._hovered_at
        self._pointer = (x, y)
        if button is None:
            return
        if self._drop_unhovered_clicks and not was_hovered:
            self.dropped_clicks += 1
            return
        target = current.on_click.get(button)
        if target is not None:
            self._go(target, clicked=current.buttons[button])

    def key(self, name: str) -> None:
        self.log.append(("key", name))
        target = self._states[self.state].on_key.get(name)
        if target is not None:
            self._go(target, clicked=None)

    def now(self) -> float:
        return self._time

    def sleep(self, seconds: float) -> None:
        self._time += seconds

    # ------------------------------------------------------------ for tests

    @property
    def clicks(self) -> list[tuple]:
        return [entry for entry in self.log if entry[0] == "click"]

    @property
    def misses(self) -> list[tuple]:
        """Clicks that landed on no button of the screen they were sent to."""
        return [entry for entry in self.clicks if entry[3] is None]

    @property
    def keys(self) -> list[str]:
        return [entry[1] for entry in self.log if entry[0] == "key"]

    @property
    def elapsed(self) -> float:
        return self._time - START_TIME

    def _button_at(self, x: int, y: int) -> str | None:
        for name, box in self._states[self.state].buttons.items():
            if _inside(box, x, y):
                return name
        return None

    def _go(self, target: str, *, clicked: Box | None) -> None:
        if target == self.state:
            return
        self.state = target
        self._stale_left = self._stale_frames
        if self._stale_until_pointer_leaves and clicked is not None:
            self._held_by = clicked
        self._hovered = None


# ------------------------------------------------------------ drawn frames


def _blank(
    colour: tuple[int, int, int] = SKY,
) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", SIZE, colour)
    return im, ImageDraw.Draw(im)


def _back_arrow(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((24, 24, 38, 38), fill=WHITE)


def _back_button(draw: ImageDraw.ImageDraw) -> None:
    """A white button with a dark arrow glyph: GAME SETUP and the tribe screens."""
    draw.rectangle((14, 14, 49, 49), fill=WHITE)
    draw.rectangle((30, 30, 32, 32), fill=BLACK)


def draw_main_menu() -> Image.Image:
    im, draw = _blank()
    draw.rectangle((1180, 0, 1279, 59), fill=MENU_TOP_RIGHT)
    b = MENU_MULTIPLAYER
    draw.rectangle((b[0], b[1], b[2] - 1, b[3] - 1), fill=BUTTON_BLUE)
    return im


def draw_multiplayer() -> Image.Image:
    im, draw = _blank()
    draw.rectangle((0, 72, 1279, 119), fill=TAB_BAND)
    draw.rectangle((580, 85, 700, 117), fill=WHITE)  # the selected tab's pill
    draw.rectangle((712, 85, 819, 117), fill=WHITE)  # Replays, right of it
    draw.rectangle((660, 660, 679, 679), fill=AMBER)
    _back_arrow(draw)
    return im


def _panel(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((466, 195, 813, 524), fill=BLACK)


def _fill(draw: ImageDraw.ImageDraw, box: Box, colour: tuple[int, int, int]) -> None:
    draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=colour)


def draw_invitation() -> Image.Image:
    im = draw_multiplayer()
    draw = ImageDraw.Draw(im)
    _panel(draw)
    _fill(draw, INVITE_DECLINE, RED)
    _fill(draw, INVITE_BACK, GREY)
    _fill(draw, INVITE_ACCEPT, BUTTON_BLUE)
    return im


def draw_lobby() -> Image.Image:
    """A lobby's panel: START GAME reaches further left than ACCEPT does."""
    im = draw_multiplayer()
    draw = ImageDraw.Draw(im)
    _panel(draw)
    _fill(draw, LOBBY_LEAVE, RED)
    _fill(draw, LOBBY_START_GAME, BUTTON_BLUE)
    return im


def draw_settings() -> Image.Image:
    im, draw = _blank((40, 40, 60))
    draw.rectangle((0, 0, 1279, 59), fill=MAROON)
    _back_arrow(draw)
    return im


def draw_profile() -> Image.Image:
    """Player Profile: the list's header and arrow, no tab band, a teal icon."""
    im, draw = _blank()
    draw.rectangle((660, 660, 679, 679), fill=TEAL)
    _back_arrow(draw)
    return im


def draw_game_setup() -> Image.Image:
    im, draw = _blank()
    _back_button(draw)
    return im


def _tribe_grid(xs: list[int], ys: list[int], *, below: int) -> Image.Image:
    im, draw = _blank()
    _back_button(draw)
    for x in xs:
        for y in ys:
            draw.ellipse((x - 30, y - 30, x + 30, y + below), fill=CIRCLE_BLUE)
    return im


def draw_tribe() -> Image.Image:
    """PICK YOUR TRIBE as creating a game shows it: tall circles."""
    return _tribe_grid(screen.TRIBE_X, screen.TRIBE_Y, below=48)


def draw_accept_tribe() -> Image.Image:
    """PICK YOUR TRIBE as accepting shows it: the grid higher, circles round."""
    return _tribe_grid(screen.ACCEPT_TRIBE_X, screen.ACCEPT_TRIBE_Y, below=30)


def draw_unknown() -> Image.Image:
    im, draw = _blank((10, 60, 10))
    draw.rectangle((0, 0, 400, 400), fill=GREEN)
    return im


def without_exits(states: dict[str, State], name: str) -> dict[str, State]:
    """The states, with one whose buttons and keys now lead nowhere."""
    stuck = states[name]
    return {**states, name: State(name, stuck.frame, buttons=stuck.buttons)}


def seat_states() -> dict[str, State]:
    """The screens the client can be found on, and how each is left.

    ``multiplayer_after_accept`` is the list as it is after an invitation was
    accepted: its back arrow reopens the accept side's tribe screen, whose own
    back arrow returns to a list that then leads to the main menu.
    """
    states = [
        State(
            "main_menu",
            draw_main_menu(),
            buttons={"multiplayer": MENU_MULTIPLAYER},
            on_click={"multiplayer": "multiplayer"},
        ),
        State(
            "multiplayer",
            draw_multiplayer(),
            buttons={"back": BACK_ARROW},
            on_click={"back": "main_menu"},
            on_key={"Escape": "settings"},
        ),
        State(
            "multiplayer_after_accept",
            draw_multiplayer(),
            buttons={"back": BACK_ARROW},
            on_click={"back": "accept_tribe"},
            on_key={"Escape": "settings"},
        ),
        State(
            "accept_tribe",
            draw_accept_tribe(),
            buttons={"back": BACK_ARROW},
            on_click={"back": "multiplayer"},
        ),
        State(
            "invitation",
            draw_invitation(),
            buttons={"back": INVITE_BACK, "accept": INVITE_ACCEPT},
            forbidden={"decline": INVITE_DECLINE},
            on_click={"back": "multiplayer"},
        ),
        State(
            "lobby",
            draw_lobby(),
            forbidden={"start_game": LOBBY_START_GAME, "delete": LOBBY_LEAVE},
        ),
        State(
            "settings",
            draw_settings(),
            buttons={"back": BACK_ARROW},
            forbidden={"settings": SETTINGS_BODY},
            on_click={"back": "main_menu"},
        ),
        State(
            "game_setup",
            draw_game_setup(),
            buttons={"back": BACK_ARROW},
            on_click={"back": "multiplayer"},
        ),
        State(
            "tribe",
            draw_tribe(),
            buttons={"back": BACK_ARROW},
            on_click={"back": "game_setup"},
        ),
        State(
            "profile",
            draw_profile(),
            buttons={"back": BACK_ARROW},
            forbidden={"profile": PROFILE_BODY},
            on_click={"back": "main_menu"},
        ),
        State("unknown", draw_unknown()),
        State("dialog", draw_unknown(), on_key={"Escape": "main_menu"}),
    ]
    return {state.name: state for state in states}
