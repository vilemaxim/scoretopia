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

A state can also change by itself after a time (``after``), as a replay does
when it reaches its last turn, and by the wheel (``on_scroll``). A state's
frame may be a function, drawn when first shown.

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

from collections.abc import Callable, Mapping
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
#: The Multiplayer list's Profile icon. A replay's Exit and Finish Game sit
#: at the same point.
PROFILE_ICON: Box = (745, 645, 785, 685)
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
    #: The frame, or a function that draws it when the state is first shown.
    frame: Image.Image | Callable[[], Image.Image]
    #: Button name to its box on the screen.
    buttons: Mapping[str, Box] = field(default_factory=dict)
    #: Forbidden kind (one of FORBIDDEN_KINDS) to the box it covers.
    forbidden: Mapping[str, Box] = field(default_factory=dict)
    #: Button name to the state a click on it leads to.
    on_click: Mapping[str, str] = field(default_factory=dict)
    #: Key name to the state pressing it leads to.
    on_key: Mapping[str, str] = field(default_factory=dict)
    #: "up" or "down" to the state the wheel leads to.
    on_scroll: Mapping[str, str] = field(default_factory=dict)
    #: Seconds in this state, and the state it then becomes by itself.
    after: tuple[float, str] | None = None

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
        self._entered_at = self._time
        self._drawn: dict[str, Image.Image] = {}
        #: Every state the screen has been in, in order.
        self.visited: list[str] = [start]

    # ------------------------------------------------------------ the seam

    def seat(self, **settings: float) -> Seat:
        return Seat(frames=self, inputs=self, clock=self, **settings)

    def grab(self) -> Image.Image:
        self._tick()
        self.frames_served += 1
        if self._hovered is not None:
            self.hovering_grabs += 1
        if self._held_by is not None:
            return self._frame(self._shown)
        if self._stale_left > 0:
            self._stale_left -= 1
            return self._frame(self._shown)
        self._shown = self.state
        return self._frame(self.state)

    def _frame(self, name: str) -> Image.Image:
        if name not in self._drawn:
            frame = self._states[name].frame
            self._drawn[name] = frame() if callable(frame) else frame
        return self._drawn[name].copy()

    def _tick(self) -> None:
        """Apply every timed change that is due."""
        while True:
            after = self._states[self.state].after
            if after is None or self._time - self._entered_at < after[0]:
                return
            due = self._entered_at + after[0]
            self._go(after[1], clicked=None)
            self._entered_at = due

    def move(self, x: int, y: int) -> None:
        self._tick()
        self.log.append(("move", x, y))
        self._pointer = (x, y)
        self._hovered = self._button_at(x, y)
        self._hovered_at = self._time
        if self._held_by is not None and not _inside(self._held_by, x, y):
            self._held_by = None

    def click(self, x: int, y: int) -> None:
        self._tick()
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
        self._tick()
        self.log.append(("key", name))
        target = self._states[self.state].on_key.get(name)
        if target is not None:
            self._go(target, clicked=None)

    def scroll(self, x: int, y: int, notches: int) -> None:
        self._tick()
        self.log.append(("scroll", x, y, notches))
        self._pointer = (x, y)
        direction = "down" if notches > 0 else "up"
        # Three notches are one step of the list.
        for _ in range(max(1, round(abs(notches) / 3))):
            target = self._states[self.state].on_scroll.get(direction)
            if target is not None:
                self._go(target, clicked=None)
        self._hovered = self._button_at(x, y)
        self._hovered_at = self._time

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
        self.visited.append(target)
        self._entered_at = self._time
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


def draw_multiplayer(tab: str = "ongoing") -> Image.Image:
    im, draw = _blank()
    draw.rectangle((0, 72, 1279, 119), fill=TAB_BAND)
    draw.rectangle((580, 85, 700, 117), fill=WHITE)  # the selected tab's pill
    if tab == "ongoing":
        draw.rectangle((712, 85, 819, 117), fill=WHITE)  # Replays, right of it
    else:
        draw.rectangle((455, 85, 569, 117), fill=WHITE)  # Ongoing, left of it
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
    # The label: white across the button's centre, as the live one's text is.
    _fill(draw, (728, 490, 773, 507), WHITE)
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
            forbidden={"profile": PROFILE_ICON},
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


# ------------------------------------------------------------ the Replays tab

ROW = (49, 26, 23)
GAME_VIEW = (30, 70, 40)
END_SCREEN = (20, 30, 40)
EXIT_BLUE = (0, 153, 255)
FINISH_GREEN = (60, 200, 80)

#: A row's height, the distance from one row's top to the next, and the first
#: row's top with the list at its top.
ROW_HEIGHT, ROW_PITCH, FIRST_ROW_TOP = 60, 77, 185
#: The list shows between the tab bar and the bottom bar: the span the screen
#: layer reads rows in. The live list lies over the menu scenery lower down,
#: where a row can read as split; the fake has no scenery.
LIST_TOP, LIST_BOTTOM = 130, 626
#: How far one call of three wheel notches moves the list.
WHEEL_STEP = 50

ONGOING_TAB: Box = (455, 85, 570, 118)
REPLAYS_TAB: Box = (712, 85, 820, 118)
PANEL: Box = (466, 177, 814, 545)
PANEL_BACK: Box = (477, 500, 556, 533)
PANEL_OPEN: Box = (720, 500, 801, 533)
PANEL_INFO: Box = (660, 234, 693, 267)
#: The panel's other icons. One of them deletes the game.
PANEL_OTHER_ICONS: Box = (480, 234, 650, 267)
INFO_BACK: Box = (600, 400, 681, 437)
REPLAY_CONTROL: Box = PROFILE_ICON
VICTORY_DONE: Box = (594, 395, 687, 432)
RESIGNED_DONE: Box = (594, 418, 687, 456)


@dataclass(frozen=True)
class FinishedGame:
    """A row of the Replays tab, and how its replay plays out."""

    #: Which of the drawn text patterns its row carries; rows differ by it.
    pattern: int
    #: Seconds the replay plays before Finish Game shows.
    plays_for: float = 40
    #: "victory" or "resigned": the end screen Finish Game leads to.
    ending: str = "victory"


def _row_text(
    draw: ImageDraw.ImageDraw, top: int, pattern: int, left: int = 479
) -> None:
    """A block of white where a row's text is; its place tells rows apart.

    ``left`` is the x of the row's left edge on what is drawn on.
    """
    x = left + 66 + 46 * (pattern % 4)
    y = top + 10 + 22 * (pattern // 4 % 2)
    draw.rectangle((x, y, x + 39, y + 13), fill=WHITE)


def scroll_offsets(rows: int) -> list[int]:
    """The list's positions, in wheel steps from its top to its end."""
    travel = 0 if rows < 5 else 100 + ROW_PITCH * (rows - 5)
    offsets = list(range(0, travel, WHEEL_STEP))
    return offsets + [travel]


def _row_span(index: int, offset: int) -> tuple[int, int] | None:
    """The visible part of a row, top and bottom, or None when none shows."""
    top = FIRST_ROW_TOP + ROW_PITCH * index - offset
    seen = (max(top, LIST_TOP), min(top + ROW_HEIGHT, LIST_BOTTOM))
    return seen if seen[0] < seen[1] else None


def draw_replays(games: list[FinishedGame], offset: int) -> Image.Image:
    im = draw_multiplayer("replays")
    for index, game in enumerate(games):
        if _row_span(index, offset) is None:
            continue
        top = FIRST_ROW_TOP + ROW_PITCH * index - offset
        row = Image.new("RGB", (322, ROW_HEIGHT), ROW)
        _row_text(ImageDraw.Draw(row), 0, game.pattern, left=0)
        seen = _row_span(index, offset)
        im.paste(row.crop((0, seen[0] - top, 322, seen[1] - top)), (479, seen[0]))
    return im


def draw_replay_panel(game: FinishedGame) -> Image.Image:
    im = draw_multiplayer("replays")
    draw = ImageDraw.Draw(im)
    _fill(draw, PANEL, BLACK)
    _row_text(draw, 290, game.pattern)
    _fill(draw, PANEL_INFO, WHITE)
    _fill(draw, PANEL_OTHER_ICONS, GREY)
    _fill(draw, PANEL_BACK, GREY)
    _fill(draw, PANEL_OPEN, BUTTON_BLUE)
    return im


def draw_game_info(game: FinishedGame) -> Image.Image:
    im = draw_replay_panel(game)
    draw = ImageDraw.Draw(im)
    _fill(draw, (480, 280, 800, 480), BLACK)
    _row_text(draw, 300, game.pattern)
    _fill(draw, INFO_BACK, BUTTON_BLUE)
    return im


def draw_loading() -> Image.Image:
    im, draw = _blank(BLACK)
    draw.rectangle((560, 340, 720, 380), fill=(90, 90, 90))
    return im


def draw_replay(control: tuple[int, int, int]) -> Image.Image:
    im, draw = _blank(GAME_VIEW)
    _fill(draw, REPLAY_CONTROL, control)
    return im


def draw_end(game: FinishedGame) -> Image.Image:
    im, draw = _blank(END_SCREEN)
    draw.rectangle((0, 0, 1279, 59), fill=(9, 8, 9))
    _row_text(draw, 200, game.pattern)
    done = VICTORY_DONE if game.ending == "victory" else RESIGNED_DONE
    _fill(draw, done, BUTTON_BLUE)
    return im


def replay_states(
    games: list[FinishedGame], *, loads_for: float = 8, ongoing: str = "multiplayer"
) -> dict[str, State]:
    """The seat's screens with these finished games on the Replays tab.

    The list is ``replays@<offset>``. A game's states are ``panel``, ``info``,
    ``loading``, ``playing``, ``finish``, and ``end``, each named
    ``<state>:<game index>@<offset>``, the offset being where the list returns
    to. ``ongoing`` names the state the Ongoing tab leads to.
    """
    states = seat_states()
    offsets = scroll_offsets(len(games))
    menu = states["main_menu"]
    states["main_menu"] = State(
        "main_menu", menu.frame, buttons=menu.buttons, on_click=menu.on_click
    )
    ongoing = states["multiplayer"]
    states["multiplayer"] = State(
        "multiplayer",
        ongoing.frame,
        buttons={**ongoing.buttons, "replays_tab": REPLAYS_TAB},
        forbidden=ongoing.forbidden,
        on_click={**ongoing.on_click, "replays_tab": f"replays@{offsets[-1]}"},
        on_key=ongoing.on_key,
    )
    for at, offset in enumerate(offsets):
        rows = {}
        for index in range(len(games)):
            seen = _row_span(index, offset)
            if seen is not None:
                rows[f"row{index}"] = (479, seen[0], 801, seen[1])
        scroll = {}
        if at > 0:
            scroll["up"] = f"replays@{offsets[at - 1]}"
        if at < len(offsets) - 1:
            scroll["down"] = f"replays@{offsets[at + 1]}"
        states[f"replays@{offset}"] = State(
            f"replays@{offset}",
            lambda offset=offset: draw_replays(games, offset),
            buttons={"back": BACK_ARROW, "ongoing_tab": ONGOING_TAB, **rows},
            forbidden={"profile": PROFILE_ICON},
            on_click={
                "back": "main_menu",
                "ongoing_tab": ongoing,
                **{name: f"panel:{name[3:]}@{offset}" for name in rows},
            },
            on_key={"Escape": "settings"},
            on_scroll=scroll,
        )
        for index, game in enumerate(games):
            tag = f"{index}@{offset}"
            done = VICTORY_DONE if game.ending == "victory" else RESIGNED_DONE
            for state in (
                State(
                    f"panel:{tag}",
                    lambda game=game: draw_replay_panel(game),
                    buttons={
                        "panel_back": PANEL_BACK,
                        "open": PANEL_OPEN,
                        "more_info": PANEL_INFO,
                    },
                    forbidden={"delete": PANEL_OTHER_ICONS},
                    on_click={
                        "panel_back": f"replays@{offset}",
                        "open": f"loading:{tag}",
                        "more_info": f"info:{tag}",
                    },
                ),
                State(
                    f"info:{tag}",
                    lambda game=game: draw_game_info(game),
                    buttons={"info_back": INFO_BACK},
                    forbidden={"delete": PANEL_OTHER_ICONS},
                    on_click={"info_back": f"panel:{tag}"},
                ),
                State(
                    f"loading:{tag}", draw_loading, after=(loads_for, f"playing:{tag}")
                ),
                State(
                    f"playing:{tag}",
                    lambda: draw_replay(EXIT_BLUE),
                    buttons={"exit": REPLAY_CONTROL},
                    on_click={"exit": f"replays@{offset}"},
                    after=(game.plays_for, f"finish:{tag}"),
                ),
                State(
                    f"finish:{tag}",
                    lambda: draw_replay(FINISH_GREEN),
                    buttons={"finish_game": REPLAY_CONTROL},
                    on_click={"finish_game": f"end:{tag}"},
                ),
                State(
                    f"end:{tag}",
                    lambda game=game: draw_end(game),
                    buttons={"done": done},
                    on_click={"done": f"replays@{offset}"},
                ),
            ):
                states[state.name] = state
    return states


# ------------------------------------------------------------ the Ongoing tab

#: More info on a card's panel: the last of its icons.
CARD_INFO: Box = (733, 234, 766, 267)
#: The panel's icons left of More info. One of them deletes the game.
CARD_OTHER_ICONS: Box = (480, 234, 725, 267)
#: The one button of a joined lobby's panel: a blue BACK at the centre.
LOBBY_BACK: Box = (600, 500, 681, 533)

#: What an Ongoing card can be. ``started`` is a game in progress. ``lobby``
#: is a game the seat has joined that has not started. ``invitation`` is one
#: it has not answered. ``own_lobby`` is a lobby the seat itself created, whose
#: panel offers START GAME where a started game's has OPEN.
CARD_KINDS = ("started", "lobby", "invitation", "own_lobby")

#: A card under "Your Turn" or "Game Invitations"; the others are ``ROW``.
TURN_BLUE = (49, 146, 228)
#: A started game's panel carries a red RESIGN of its own on the seat's turn.
#: Seat control resigns in the game, so this is no button: a click is a miss.
PANEL_RESIGN: Box = (725, 197, 807, 219)
#: The robot portrait of a player who resigned, and its eyes.
ROBOT: Box = (655, 373, 686, 414)
ROBOT_EYES: Box = (662, 385, 678, 388)
ROBOT_GREY = (170, 170, 170)

#: The game view's Settings button, and what in-game Settings shows.
GAME_SETTINGS: Box = (497, 647, 534, 684)
GAME_BACK: Box = (14, 14, 50, 50)
GAME_RESIGN: Box = (1195, 16, 1256, 45)
GAME_EXIT: Box = (590, 562, 691, 597)
#: Everything in-game Settings can change.
GAME_OPTIONS: Box = (300, 80, 980, 520)
CONFIRM_OK: Box = (751, 384, 805, 424)
CONFIRM_BACK: Box = (490, 390, 537, 419)
#: The turn 0 dialog's OK, on a game the seat created and on one it accepted.
TURN0_OK: Mapping[str, Box] = {
    "created": (600, 442, 681, 473),
    "accepted": (600, 513, 681, 544),
}
DIM_VIEW = (12, 28, 16)
DIM_RED = (90, 20, 0)


@dataclass(frozen=True)
class OngoingGame:
    """A card of the Ongoing tab."""

    #: Which of the drawn title patterns its card carries; cards differ by it.
    pattern: int
    kind: str = "started"
    #: A started game on the seat's turn: its card is drawn blue, and OPEN on
    #: its panel leads into the game.
    seat_turn: bool = False
    #: The other player has resigned: the panel shows a robot's portrait.
    opponent_resigned: bool = False
    #: Where the turn 0 dialog's OK shows once the game has loaded: "created",
    #: "accepted", or None for no dialog.
    turn0: str | None = "created"

    def __post_init__(self) -> None:
        if self.kind not in CARD_KINDS:
            raise ValueError(f"not a card kind: {self.kind}")
        if self.turn0 not in (None, *TURN0_OK):
            raise ValueError(f"not a turn 0 dialog: {self.turn0}")

    @property
    def blue(self) -> bool:
        """Whether the card is drawn blue, as under "Your Turn"."""
        return self.kind == "invitation" or (self.kind == "started" and self.seat_turn)


def _card_title(
    draw: ImageDraw.ImageDraw, top: int, pattern: int, left: int = 479
) -> None:
    """A block of white on a card's title line; its place tells cards apart."""
    x = left + 62 + 40 * (pattern % 5)
    y = top + 9 + 13 * (pattern // 5 % 2)
    draw.rectangle((x, y, x + 35, y + 12), fill=WHITE)


def draw_ongoing(games: list[OngoingGame], offset: int) -> Image.Image:
    im = draw_multiplayer("ongoing")
    for index, game in enumerate(games):
        seen = _row_span(index, offset)
        if seen is None:
            continue
        top = FIRST_ROW_TOP + ROW_PITCH * index - offset
        card = Image.new("RGB", (322, ROW_HEIGHT), TURN_BLUE if game.blue else ROW)
        _card_title(ImageDraw.Draw(card), 0, game.pattern, left=0)
        im.paste(card.crop((0, seen[0] - top, 322, seen[1] - top)), (479, seen[0]))
    return im


def draw_card_panel(game: OngoingGame) -> Image.Image:
    """A started game's panel: grey BACK, blue OPEN, More info last of its icons."""
    im = draw_multiplayer("ongoing")
    draw = ImageDraw.Draw(im)
    _fill(draw, PANEL, BLACK)
    _card_title(draw, 290, game.pattern)
    _fill(draw, CARD_OTHER_ICONS, GREY)
    _fill(draw, CARD_INFO, WHITE)
    _fill(draw, PANEL_BACK, GREY)
    _fill(draw, PANEL_OPEN, BUTTON_BLUE)
    if game.seat_turn:
        _fill(draw, PANEL_RESIGN, RED)
    if game.opponent_resigned:
        _fill(draw, ROBOT, ROBOT_GREY)
        _fill(draw, ROBOT_EYES, RED)
    return im


def draw_game_view(turn0: str | None = None) -> Image.Image:
    """A game as OPEN shows it, with the turn 0 dialog over it when asked."""
    im, draw = _blank(GAME_VIEW)
    _fill(draw, GAME_SETTINGS, GREY)
    if turn0 is not None:
        _fill(draw, (462, 259, 816, 560), BLACK)
        _fill(draw, TURN0_OK[turn0], CIRCLE_BLUE)
    return im


def draw_game_settings(*, confirm: bool = False) -> Image.Image:
    """In-game Settings; with ``confirm``, the Resign confirmation over it."""
    im, draw = _blank(DIM_VIEW)
    _fill(draw, GAME_BACK, (188, 188, 188))
    draw.rectangle((30, 30, 32, 32), fill=WHITE)
    draw.rectangle((540, 538, 739, 542), fill=(166, 166, 166))
    _fill(draw, GAME_EXIT, (8, 131, 214))
    _fill(draw, GAME_RESIGN, DIM_RED if confirm else RED)
    if confirm:
        _fill(draw, (462, 300, 816, 440), BLACK)
        _fill(draw, CONFIRM_BACK, WHITE)
        _fill(draw, CONFIRM_OK, CIRCLE_BLUE)
    return im


def draw_card_info(game: OngoingGame) -> Image.Image:
    im = draw_card_panel(game)
    draw = ImageDraw.Draw(im)
    _fill(draw, (480, 280, 800, 480), BLACK)
    _card_title(draw, 300, game.pattern)
    _fill(draw, INFO_BACK, BUTTON_BLUE)
    return im


def draw_joined_lobby(game: OngoingGame) -> Image.Image:
    """The panel of a game that has not started: no OPEN, a blue BACK."""
    im = draw_multiplayer("ongoing")
    draw = ImageDraw.Draw(im)
    _fill(draw, PANEL, BLACK)
    _card_title(draw, 290, game.pattern)
    _fill(draw, LOBBY_BACK, BUTTON_BLUE)
    return im


def draw_wide_button_panel(box: Box) -> Image.Image:
    """A panel no card is known to open, with one blue button at ``box``."""
    im = draw_multiplayer("ongoing")
    draw = ImageDraw.Draw(im)
    _fill(draw, PANEL, BLACK)
    _fill(draw, box, BUTTON_BLUE)
    return im


#: Slot 1 of the accept side's tribe grid, and PICK on the tribe's panel.
ACCEPT_TRIBE_1: Box = (459, 194, 520, 255)
TRIBE_PICK: Box = (730, 562, 809, 597)
#: Slot 1 where the grid sits when creating a game.
CREATE_TRIBE_1: Box = (458, 210, 519, 271)
#: A joined lobby's LEAVE, top right of its panel.
JOINED_LEAVE: Box = (720, 189, 802, 211)


def draw_tribe_pick() -> Image.Image:
    """The accept side's tribe screen with a tribe's panel open and PICK lit."""
    im = draw_accept_tribe()
    draw = ImageDraw.Draw(im)
    _fill(draw, (466, 150, 814, 610), BLACK)
    _fill(draw, TRIBE_PICK, BUTTON_BLUE)
    return im


def draw_invited_lobby(game: OngoingGame) -> Image.Image:
    """A joined lobby's panel as it shows after an accept: BACK, and LEAVE."""
    im = draw_joined_lobby(game)
    _fill(ImageDraw.Draw(im), JOINED_LEAVE, RED)
    return im


#: Marks the states of the list as it is once an invitation has been accepted
#: in this client session.
AFTER_ACCEPT = "!"


def ongoing_states(
    games: list[OngoingGame],
    finished: list[FinishedGame] | None = None,
    *,
    resigned_to: str | None = None,
) -> dict[str, State]:
    """The seat's screens with these cards on the Ongoing tab.

    The list is ``ongoing@<offset>``, and the main menu's MULTIPLAYER opens it
    at its end. A card's panel is ``card:<index>@<offset>`` whatever its kind,
    and a started game's Game Info is ``cardinfo:<index>@<offset>``. The
    Replays tab holds ``finished``.

    ACCEPT on an invitation leads through ``accepttribe``, ``pick``, and
    ``joined``, each named as the card's panel is. From then on the list's
    states carry ``!`` after the offset: its back arrow reopens the accept
    side's tribe screen (``reopened_tribe``), whose own back arrow returns to
    the unmarked list. The cards stay as drawn: an accepted invitation's card
    still opens its invitation.

    OPEN on a started game on the seat's turn leads through ``gameload``,
    ``turn0`` when the game has the dialog, ``game``, ``gamesettings``,
    ``confirm``, and ``over``, each named as the card's panel is. DONE on
    ``over`` leads to ``resigned_to``: by default the list at its end, as the
    main menu's MULTIPLAYER opens it. The resigned game's card stays as drawn.
    """
    offsets = scroll_offsets(len(games))
    home = f"ongoing@{offsets[-1]}"
    states = replay_states(finished or [], ongoing=home)
    replays = f"replays@{scroll_offsets(len(finished or []))[-1]}"
    menu = states["main_menu"]
    states["main_menu"] = State(
        "main_menu", menu.frame, buttons=menu.buttons, on_click={"multiplayer": home}
    )
    states["reopened_tribe"] = State(
        "reopened_tribe",
        draw_accept_tribe(),
        buttons={"back": BACK_ARROW},
        on_click={"back": home},
    )
    for mark in ("", AFTER_ACCEPT):
        for at, offset in enumerate(offsets):
            _add_ongoing_page(
                states, games, offsets, at, mark, replays, resigned_to or home
            )
    return states


def _add_ongoing_page(
    states: dict[str, State],
    games: list[OngoingGame],
    offsets: list[int],
    at: int,
    mark: str,
    replays: str,
    resigned_to: str,
) -> None:
    """One scroll position of the Ongoing list, and its cards' panels."""
    offset = offsets[at]
    cards = {}
    for index in range(len(games)):
        seen = _row_span(index, offset)
        if seen is not None:
            cards[f"card{index}"] = (479, seen[0], 801, seen[1])
    scroll = {}
    if at > 0:
        scroll["up"] = f"ongoing@{offsets[at - 1]}{mark}"
    if at < len(offsets) - 1:
        scroll["down"] = f"ongoing@{offsets[at + 1]}{mark}"
    here = f"ongoing@{offset}{mark}"
    states[here] = State(
        here,
        lambda: draw_ongoing(games, offset),
        buttons={"back": BACK_ARROW, "replays_tab": REPLAYS_TAB, **cards},
        forbidden={"profile": PROFILE_ICON},
        on_click={
            "back": "reopened_tribe" if mark else "main_menu",
            "replays_tab": replays,
            **{name: f"card:{name[4:]}@{offset}{mark}" for name in cards},
        },
        on_key={"Escape": "settings"},
        on_scroll=scroll,
    )
    for index, game in enumerate(games):
        tag = f"{index}@{offset}{mark}"
        for state in _card_states(
            game, tag, here, f"ongoing@{offset}{AFTER_ACCEPT}", resigned_to
        ):
            states[state.name] = state


def _game_states(game: OngoingGame, tag: str, resigned_to: str) -> list[State]:
    """The game OPEN leads into, as far as its end screen after a resign."""
    loaded = f"turn0:{tag}" if game.turn0 else f"game:{tag}"
    states = [
        State(f"gameload:{tag}", draw_loading, after=(8, loaded)),
        State(
            f"game:{tag}",
            draw_game_view,
            buttons={"game_settings": GAME_SETTINGS},
            on_click={"game_settings": f"gamesettings:{tag}"},
        ),
        State(
            f"gamesettings:{tag}",
            draw_game_settings,
            buttons={"resign": GAME_RESIGN, "back": GAME_BACK, "exit": GAME_EXIT},
            forbidden={"settings": GAME_OPTIONS},
            on_click={"resign": f"confirm:{tag}", "back": f"game:{tag}"},
        ),
        State(
            f"confirm:{tag}",
            lambda: draw_game_settings(confirm=True),
            buttons={"confirm_ok": CONFIRM_OK, "confirm_back": CONFIRM_BACK},
            on_click={
                "confirm_ok": f"over:{tag}",
                "confirm_back": f"gamesettings:{tag}",
            },
        ),
        State(
            f"over:{tag}",
            lambda: draw_end(FinishedGame(game.pattern)),
            buttons={"done": VICTORY_DONE},
            on_click={"done": resigned_to},
        ),
    ]
    if game.turn0:
        states.append(
            State(
                f"turn0:{tag}",
                lambda: draw_game_view(game.turn0),
                buttons={"turn0_ok": TURN0_OK[game.turn0]},
                on_click={"turn0_ok": f"game:{tag}"},
            )
        )
    return states


def _card_states(
    game: OngoingGame, tag: str, here: str, after_accept: str, resigned_to: str
) -> list[State]:
    """A card's panel and what it leads to; ``here`` is the list under it."""
    if game.kind == "started":
        into_game = (
            {"open": f"gameload:{tag}"}
            if game.seat_turn and not game.opponent_resigned
            else {}
        )
        return [
            *(_game_states(game, tag, resigned_to) if into_game else []),
            State(
                f"card:{tag}",
                lambda: draw_card_panel(game),
                buttons={
                    "panel_back": PANEL_BACK,
                    "open": PANEL_OPEN,
                    "more_info": CARD_INFO,
                },
                forbidden={"delete": CARD_OTHER_ICONS},
                on_click={
                    "panel_back": here,
                    "more_info": f"cardinfo:{tag}",
                    **into_game,
                },
            ),
            State(
                f"cardinfo:{tag}",
                lambda: draw_card_info(game),
                buttons={"info_back": INFO_BACK},
                forbidden={"delete": CARD_OTHER_ICONS},
                on_click={"info_back": f"card:{tag}"},
            ),
        ]
    if game.kind == "lobby":
        return [
            State(
                f"card:{tag}",
                lambda: draw_joined_lobby(game),
                buttons={"lobby_back": LOBBY_BACK},
                on_click={"lobby_back": here},
            )
        ]
    if game.kind == "invitation":
        return [
            State(
                f"card:{tag}",
                draw_invitation,
                buttons={"back": INVITE_BACK, "accept": INVITE_ACCEPT},
                forbidden={"decline": INVITE_DECLINE},
                on_click={"back": here, "accept": f"accepttribe:{tag}"},
            ),
            State(
                f"accepttribe:{tag}",
                draw_accept_tribe,
                buttons={"tribe1": ACCEPT_TRIBE_1},
                on_click={"tribe1": f"pick:{tag}"},
            ),
            State(
                f"pick:{tag}",
                draw_tribe_pick,
                buttons={"pick": TRIBE_PICK},
                on_click={"pick": f"joined:{tag}"},
            ),
            State(
                f"joined:{tag}",
                lambda: draw_invited_lobby(game),
                buttons={"lobby_back": LOBBY_BACK},
                forbidden={"delete": JOINED_LEAVE},
                on_click={"lobby_back": after_accept},
            ),
        ]
    return [
        State(
            f"card:{tag}",
            draw_lobby,
            forbidden={"start_game": LOBBY_START_GAME, "delete": LOBBY_LEAVE},
        )
    ]
