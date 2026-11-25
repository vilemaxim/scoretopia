"""The seat's screen layer, on frames drawn in code.

No display, no game client, and no recorded frame: every image here is
painted by the test, so these run everywhere. Recorded frames are covered by
``test_seat_screen_frames.py``, which runs only where the frames are.
"""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image, ImageDraw

from scoretopia.seat import screen

SIZE = (1280, 720)
SKY = (245, 103, 169)
CARD = (49, 26, 23)
WHITE = (255, 255, 255)


def _frame(colour: tuple[int, int, int] = SKY) -> Image.Image:
    return Image.new("RGB", SIZE, colour)


def _card(im: Image.Image, top: int, *, text_at: int = 560) -> tuple[int, int]:
    """Paint a 320 px wide, 60 px tall card with a block of white 'text'."""
    draw = ImageDraw.Draw(im)
    draw.rectangle((479, top, 800, top + 59), fill=CARD)
    draw.rectangle((text_at, top + 14, text_at + 60, top + 30), fill=WHITE)
    return (top, top + 60)


def test_the_module_imports_and_names_a_flat_frame_without_a_display() -> None:
    assert screen.flat(_frame((0, 0, 0)))
    assert screen.screen_name(_frame((0, 0, 0))) == "flat"


def test_a_frame_no_test_recognises_is_unknown() -> None:
    im = _frame((10, 60, 10))
    ImageDraw.Draw(im).rectangle((0, 0, 400, 400), fill=(90, 200, 90))

    assert screen.screen_name(im) == "unknown"


def test_bands_finds_each_card_and_ignores_a_sliver() -> None:
    im = _frame()
    first = _card(im, 200)
    second = _card(im, 300)
    ImageDraw.Draw(im).rectangle((479, 420, 800, 430), fill=CARD)

    assert screen.bands(im) == [first, second]


def test_a_sliver_at_the_lists_top_edge_is_a_cut_band() -> None:
    im = _frame()
    ImageDraw.Draw(im).rectangle((479, 130, 800, 144), fill=CARD)

    assert screen.bands(im) == []
    assert screen.cut_bands(im) == [(130, 145)]


def test_a_cards_key_follows_its_text_and_not_its_colour() -> None:
    dark, blue = _frame(), _frame()
    band = _card(dark, 200)
    ImageDraw.Draw(blue).rectangle((479, 200, 800, 259), fill=(49, 140, 228))
    ImageDraw.Draw(blue).rectangle((560, 214, 620, 230), fill=WHITE)
    other = _frame()
    _card(other, 200, text_at=640)

    assert screen.key_of(dark, band) == screen.key_of(blue, band)
    assert screen.key_of(dark, band) != screen.key_of(other, band)


def test_a_card_with_no_white_text_has_no_key() -> None:
    im = _frame()
    ImageDraw.Draw(im).rectangle((479, 200, 800, 259), fill=CARD)

    assert screen.mask_of(im, (200, 260)) is None
    assert screen.key_of(im, (200, 260)) is None


def _striped_card(im: Image.Image, top: int) -> None:
    """A card whose text is one 4 px stripe, 160 px wide, on a cell boundary."""
    draw = ImageDraw.Draw(im)
    draw.rectangle((479, top, 800, top + 59), fill=CARD)
    draw.rectangle((550, top + 12, 709, top + 15), fill=WHITE)


def test_a_card_is_matched_after_the_list_moves_two_pixels() -> None:
    before, after = _frame(), _frame()
    band = (200, 260)
    _striped_card(before, 200)
    _striped_card(after, 202)
    recorded = [screen.mask_of(before, band)]

    as_read = screen.mask_of(after, band)
    aligned = screen.mask_variants(after, band)

    assert screen.hamming(as_read, recorded[0]) > screen.KEY_TOLERANCE
    assert screen.match(as_read, recorded) is None
    assert screen.match(aligned, recorded) == recorded[0]


def test_a_different_card_is_not_matched() -> None:
    one, two = _frame(), _frame()
    band = _card(one, 200, text_at=545)
    _card(two, 200, text_at=670)
    recorded = [screen.mask_of(one, band)]

    assert screen.match(screen.mask_variants(two, band), recorded) is None


def test_masks_of_different_kinds_are_never_compared() -> None:
    im = _frame()
    _card(im, 200)
    band = (200, 236)

    setup = screen.mask_of(im, band, "setup")
    result = screen.mask_of(im, band, "result")

    assert len(setup) - len("setup") == len(result) - len("result")
    assert screen.hamming(setup, result) is None
    assert screen.hamming(setup, setup) == 0


def test_share_counts_only_pixels_near_the_colour() -> None:
    im = _frame((0, 0, 0))
    ImageDraw.Draw(im).rectangle((0, 0, 49, 99), fill=(0, 153, 255))

    assert screen.share(im, (0, 0, 100, 100), (0, 153, 255)) == pytest.approx(0.5)


def test_a_failed_command_is_named_by_its_tool_not_by_the_wrapper() -> None:
    error = subprocess.CalledProcessError(
        1,
        ["runuser", "-u", "someone", "--", "xdotool", "key", "Escape"],
        stderr=b"X Error of failed request:  BadWindow (invalid Window parameter)",
    )

    assert screen.failed_code(error) == "command_failed_xdotool_rc1_badwindow"


def test_a_command_that_could_not_open_the_display_says_so() -> None:
    error = subprocess.CalledProcessError(
        1, ["xwd", "-root"], stderr=b"xwd: unable to open display ':0'"
    )

    assert screen.failed_code(error) == "command_failed_xwd_rc1_nodisplay"
