"""Every screen test against every labelled recorded frame.

Recorded frames show player names, so they are never committed. This test
looks for them in the directory named by ``SCORETOPIA_SEAT_FRAMES``, or in the
gitignored ``samples/seat-frames/``, and is skipped where there are none.

A frame ``<stem>.png`` is labelled by ``<stem>.json`` beside it. A label says
what a person saw on the frame, never what the code returned for it, and holds
no name:

    {
      "what": "the invitation panel over the Ongoing list",
      "screen_name": "multiplayer_panel",
      "holds": ["on_multiplayer", "invite_panel"],
      "tab": "ongoing",
      "cards": 5,
      "panel": true,
      "buttons": {"INVITE_BACK": [476, 480, 556, 516]}
    }

``holds`` lists every screen test that is true on the frame; each test not
listed must be false, so every test is run against every labelled frame.
``tab``, ``cards``, ``panel``, and ``buttons`` are optional. A button names a
point constant of the screen module and the box the button fills on this
frame. A frame with no label beside it is not read.

``what`` is free text for the person labelling. Describe the screen, never who
is on it. Labels live beside the frames, outside the repository.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from scoretopia.seat import screen

SCREEN_TESTS = (
    "accept_tribe_screen",
    "exit_blue",
    "exit_ready",
    "finish_green",
    "flat",
    "game_over",
    "game_settings_screen",
    "invite_panel",
    "last_human",
    "list_back_tribe",
    "main_menu",
    "menu_badge",
    "mp_list",
    "on_multiplayer",
    "pick_ready",
    "resign_confirm",
    "resign_ready",
    "resigned_over",
    "resigned_panel",
    "settings_screen",
    "setup_end",
    "setup_screen",
    "setup_top",
    "tribe_screen",
    "victory",
)
# Functions of one frame that are not screen tests: they measure or name.
# turn0_ok() finds a button inside the game view and means nothing elsewhere:
# it is true on the main menu, whose QUIT button sits at the same point.
NOT_SCREEN_TESTS = {
    "bands",
    "cut_bands",
    "done_point",
    "menu_key",
    "note_badge",
    "panel_top",
    "screen_name",
    "selected_tab",
    "setup_box_top",
    "setup_header_bottom",
    "split_bands",
    "subtitle_hash",
    "turn0_ok",
}
# Screen tests no labelled frame shows yet. Remove a name when one does.
NO_FRAME_YET = {"menu_badge"}
LABEL_KEYS = {"what", "screen_name", "holds", "tab", "cards", "panel", "buttons"}
DEFAULT_FRAMES = Path(__file__).resolve().parent.parent / "samples" / "seat-frames"


def _frames_dir() -> Path:
    configured = os.environ.get("SCORETOPIA_SEAT_FRAMES")
    return Path(configured).expanduser() if configured else DEFAULT_FRAMES


def _labelled_frames() -> list[Path]:
    directory = _frames_dir()
    if not directory.is_dir():
        return []
    return sorted(
        label
        for label in directory.glob("*.json")
        if label.with_suffix(".png").is_file()
    )


LABELS = _labelled_frames()
needs_frames = pytest.mark.skipif(
    not LABELS,
    reason="no labelled seat frames here; they are local-only (see module docstring)",
)


def test_every_function_of_one_frame_is_listed_as_a_screen_test_or_not() -> None:
    """A screen test added to the module cannot be left out of this file."""
    of_one_frame = {
        name
        for name, function in inspect.getmembers(screen, inspect.isfunction)
        if not name.startswith("_")
        and list(inspect.signature(function).parameters) == ["im"]
    }

    assert of_one_frame == set(SCREEN_TESTS) | NOT_SCREEN_TESTS


@needs_frames
def test_every_screen_test_holds_on_some_labelled_frame() -> None:
    shown = {
        name for label in LABELS for name in json.loads(label.read_text())["holds"]
    }

    assert set(SCREEN_TESTS) - shown == NO_FRAME_YET


@pytest.fixture(params=LABELS, ids=lambda path: path.stem)
def labelled(request: pytest.FixtureRequest) -> tuple[dict, Image.Image]:
    label = json.loads(request.param.read_text())
    frame = Image.open(request.param.with_suffix(".png")).convert("RGB")
    return label, frame


@needs_frames
def test_the_label_is_well_formed(labelled: tuple[dict, Image.Image]) -> None:
    label, frame = labelled

    assert frame.size == (1280, 720)
    assert set(label) <= LABEL_KEYS
    assert {"screen_name", "holds"} <= set(label)
    assert set(label["holds"]) <= set(SCREEN_TESTS)


@needs_frames
def test_every_screen_test_agrees_with_the_label(
    labelled: tuple[dict, Image.Image],
) -> None:
    label, frame = labelled

    held = sorted(name for name in SCREEN_TESTS if bool(getattr(screen, name)(frame)))

    assert held == sorted(label["holds"])
    assert screen.screen_name(frame) == label["screen_name"]


@needs_frames
def test_the_list_reads_as_labelled(labelled: tuple[dict, Image.Image]) -> None:
    label, frame = labelled

    if "tab" in label:
        assert screen.selected_tab(frame) == label["tab"]
    if "cards" in label:
        assert len(screen.bands(frame)) == label["cards"]
    if "panel" in label:
        assert (screen.panel_top(frame) is not None) == label["panel"]


@needs_frames
def test_each_click_point_lies_inside_its_button(
    labelled: tuple[dict, Image.Image],
) -> None:
    label, _ = labelled

    for name, (left, top, right, bottom) in label.get("buttons", {}).items():
        x, y = getattr(screen, name)
        assert left <= x <= right and top <= y <= bottom, name
