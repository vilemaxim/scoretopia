"""Resigning from games where it is the seat's turn.

The seat walks the Ongoing tab. A card is on the seat's turn when it is drawn
blue, which is read from the list before anything is clicked. Such a card is
opened only when the record holds its setup (``CaptureRecord.knows()``): a
game not yet captured is left for a later run, so that every recorded game has
its players, map, and mode. A card not on the seat's turn is not opened.

From the panel of a started game the seat presses OPEN, closes the turn 0
dialog when it shows, opens the game's Settings, and presses RESIGN and the
confirmation's OK. The game's end screen follows; its DONE leads back out of
the game.

A game whose other player has resigned stays on the list as the seat's turn,
with a robot's portrait on its panel. It is over, so it is closed and counted,
not opened.

The seat resigns and never deletes: the panel's own icons and its own RESIGN
are not pressed, and nothing in the game is but the dialog's OK, Settings,
RESIGN, the confirmation's OK, and DONE.

Unconfirmed on the live client: where DONE leads from the end screen of a game
just resigned from. The playbook this replaces restarted the client there. Here
the main menu and the Multiplayer list are both taken, and any other screen
stops the action with its frame saved.

Nothing here reads a name: cards are told apart by their pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from scoretopia.seat import screen
from scoretopia.seat.actions import ScreenStop, poll_for, press, to_list, wait_for
from scoretopia.seat.capture import SETUP, open_record, walk
from scoretopia.seat.panels import OPEN_X, STARTED, Panel, close_panel, open_card
from scoretopia.seat.seam import Seat

#: Seconds the game may take to show after OPEN, and its end screen after the
#: confirmation's OK.
LOAD_WAIT = 60.0
#: Seconds the turn 0 dialog is given to show once the game does.
TURN0_WAIT = 60.0
#: Seconds one resign must have left to begin: the two loads, the dialog's
#: wait, the way back to the list, and five shorter waits.
RESIGN_BUDGET = 3 * LOAD_WAIT + TURN0_WAIT + 150
#: The game view's Settings button.
GAME_SETTINGS = (515, 665)
#: RESIGN in the game's Settings, and OK on its confirmation.
RESIGN_BUTTON = (1225, 30)
CONFIRM_OK = (777, 404)


@dataclass
class ResignCounts:
    resigned: int = 0
    #: Cards not opened because they are not drawn as the seat's turn: games
    #: on another player's turn, and lobbies.
    not_seats_turn: int = 0
    #: Cards drawn as the seat's turn, not opened because the record holds no
    #: setup for them. An invitation is drawn the same way and counts here.
    setup_missing: int = 0
    #: Games closed because their other player has resigned.
    opponent_resigned: int = 0
    #: True when the walk reached the list's end.
    list_end: bool = False
    #: True when the walk stopped because too little time was left.
    budget_reached: bool = False


def _in_game(frame: Image.Image) -> bool:
    # The main menu has blue buttons where the turn 0 dialog's OK can be.
    return not screen.on_multiplayer(frame) and not screen.main_menu(frame)


def _turn0_dialog(frame: Image.Image) -> bool:
    return _in_game(frame) and screen.turn0_ok(frame) is not None


def _out_of_game(frame: Image.Image) -> bool:
    return bool(screen.main_menu(frame) or screen.mp_list(frame))


def _resign(seat: Seat, panel: Panel, counts: ResignCounts) -> None:
    """Resign from the started game whose panel is open; ends on the list."""
    press(seat, (OPEN_X, panel.y))
    wait_for(seat, _in_game, "game_view", LOAD_WAIT)
    dialog = poll_for(seat, _turn0_dialog, TURN0_WAIT)
    if dialog is not None:
        press(seat, screen.turn0_ok(dialog))
        wait_for(
            seat,
            lambda i: _in_game(i) and screen.turn0_ok(i) is None,
            "turn0_closed",
        )
    press(seat, GAME_SETTINGS)
    wait_for(seat, screen.resign_ready, "resign_ready")
    press(seat, RESIGN_BUTTON)
    wait_for(seat, screen.resign_confirm, "resign_confirm")
    press(seat, CONFIRM_OK)
    end = wait_for(seat, screen.game_over, "resigned_over", LOAD_WAIT)
    # Resigned once the end screen shows, whatever stops the way back.
    counts.resigned += 1
    press(seat, screen.done_point(end))
    wait_for(seat, _out_of_game, "resigned_done", LOAD_WAIT)
    to_list(seat)


def resign(seat: Seat, root: Path, counts: ResignCounts) -> None:
    """Resign from every game on the seat's turn whose setup is captured.

    ``root`` is the captures root, whose record says which setups are. The
    record is read before anything is clicked. ``counts`` is filled in as the
    walk goes, so it is right whatever stops it. Raises ``Budget`` when too
    little time is left for another resign.
    """
    record = open_record(root)
    to_list(seat)

    def left_alone(
        frame: Image.Image, row: tuple[int, int], variants: list[str]
    ) -> bool:
        if not screen.bot_turn_card(frame, row):
            counts.not_seats_turn += 1
            return True
        if not record.knows(SETUP, variants):
            counts.setup_missing += 1
            return True
        return False

    def visit(row: tuple[int, int], key: str, mask: str) -> bool:
        frame, panel = open_card(seat, row)
        if panel.kind != STARTED:
            close_panel(seat, panel)
            return False
        if screen.resigned_panel(frame):
            close_panel(seat, panel)
            counts.opponent_resigned += 1
            return False
        if panel.y is None:
            raise ScreenStop("card_panel_without_open", frame)
        _resign(seat, panel, counts)
        # The list comes back wherever the client opens it.
        return True

    counts.list_end = walk(seat, SETUP, "ongoing", visit, RESIGN_BUDGET, left_alone)
