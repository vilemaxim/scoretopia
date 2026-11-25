"""The seat seam: the only way seat control reaches the game client.

Actions and runs take a ``Seat``, which carries a frame source, an input sink,
and a clock. Live, those wrap the screen-grab and input tools of the screen
layer. Under test they are a scripted fake screen, so a sequence of clicks can
be checked without the game (ADR 003).

Nothing above this module may call ``screen.grab()``, ``screen.xdo()``,
``time.time()``, or ``time.sleep()`` itself.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from scoretopia.seat import screen


class FrameSource(Protocol):
    def grab(self) -> Image.Image:
        """The screen as it is now, 1280x720 RGB."""


class InputSink(Protocol):
    def move(self, x: int, y: int) -> None:
        """Move the pointer, without clicking."""

    def click(self, x: int, y: int) -> None:
        """Left-click at a point."""

    def key(self, name: str) -> None:
        """Press and release one key, named as the input tool names it."""


class Clock(Protocol):
    def now(self) -> float:
        """Seconds since the epoch."""

    def sleep(self, seconds: float) -> None:
        """Wait."""


@dataclass(frozen=True)
class Seat:
    """A handle on the seat's game client: how an action sees and drives it.

    The seat itself is the Polytopia account (see the glossary); this is the
    one client it is signed in on.
    """

    frames: FrameSource
    inputs: InputSink
    clock: Clock
    #: Least seconds a wait for a screen lasts: the client's input lags.
    wait: float = 20
    #: Seconds between an input and the first look at its effect.
    settle: float = 2
    #: Escapes pressed, at most, to leave a screen with no known exit.
    escapes: int = 3
    #: ``clock.now()`` after which nothing more is clicked and a wait for a
    #: screen that is not showing ends; None for no limit.
    deadline: float | None = None


class LiveFrames:
    """Frames grabbed from the X display the screen layer is configured for."""

    def grab(self) -> Image.Image:
        return screen.grab()


class LiveInputs:
    """Pointer and keyboard input sent with the screen layer's input tool."""

    def move(self, x: int, y: int) -> None:
        screen.xdo("mousemove", x, y)

    def click(self, x: int, y: int) -> None:
        screen.xdo("mousemove", x, y, "click", 1)

    def key(self, name: str) -> None:
        screen.xdo("key", name)


class SystemClock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@contextmanager
def live_seat(*, display: str, x_user: str, budget_seconds: float) -> Iterator[Seat]:
    """A seat on the real game client, for as long as the block runs.

    Frames pass through a private temporary directory that is removed after.
    """
    screen.configure(display=display, x_user=x_user, budget=budget_seconds)
    clock = SystemClock()
    with screen.session():
        yield Seat(
            frames=LiveFrames(),
            inputs=LiveInputs(),
            clock=clock,
            deadline=clock.now() + budget_seconds,
        )
