"""Returning the client to the main menu, against the scripted fake screen."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fake_seat import (
    INVITE_DECLINE,
    FakeScreen,
    ForbiddenInput,
    draw_main_menu,
    draw_multiplayer,
    seat_states,
    without_exits,
)
from PIL import Image

from scoretopia.seat import actions, screen
from scoretopia.seat.seam import Seat


def _fake(start: str, **faults: object) -> FakeScreen:
    return FakeScreen(seat_states(), start, **faults)


def _point_clicks(fake: FakeScreen) -> list[str | None]:
    return [entry[3] for entry in fake.clicks]


def test_on_the_main_menu_already_nothing_is_clicked() -> None:
    fake = _fake("main_menu")

    moved = actions.to_main_menu(fake.seat())

    assert moved is False
    assert fake.clicks == []
    assert fake.keys == []


@pytest.mark.parametrize(
    ("start", "buttons"),
    [
        ("multiplayer", ["back"]),
        ("settings", ["back"]),
        ("invitation", ["back", "back"]),
        ("game_setup", ["back", "back"]),
        ("tribe", ["back", "back", "back"]),
        ("multiplayer_after_accept", ["back", "back", "back"]),
    ],
)
def test_each_known_screen_is_left_by_its_own_way_back(
    start: str, buttons: list[str]
) -> None:
    fake = _fake(start)

    moved = actions.to_main_menu(fake.seat())

    assert moved is True
    assert fake.state == "main_menu"
    assert _point_clicks(fake) == buttons
    assert fake.keys == []


def test_a_screen_with_no_known_exit_is_tried_with_escape() -> None:
    fake = _fake("dialog")

    moved = actions.to_main_menu(fake.seat())

    assert moved is True
    assert fake.state == "main_menu"
    assert fake.keys == ["Escape"]
    assert fake.clicks == []


def test_an_unknown_screen_stops_after_the_allowed_escapes() -> None:
    fake = _fake("unknown")

    with pytest.raises(screen.Stop, match="unknown_screen"):
        actions.to_main_menu(fake.seat(escapes=3))

    assert fake.keys == ["Escape"] * 3
    assert fake.clicks == []


@pytest.mark.parametrize("start", ["lobby", "profile"])
def test_a_screen_it_does_not_know_is_left_unclicked(start: str) -> None:
    fake = _fake(start)

    with pytest.raises(actions.UnknownScreen):
        actions.to_main_menu(fake.seat())

    assert fake.clicks == []


def test_an_unknown_screen_carries_the_frame_that_was_not_recognised() -> None:
    fake = _fake("unknown")

    with pytest.raises(actions.UnknownScreen) as stopped:
        actions.to_main_menu(fake.seat())

    assert screen.screen_name(stopped.value.frame) == "unknown"
    assert str(stopped.value) == "unknown_screen"


class _Frames:
    """Serves the given frames in order, then the last one for ever."""

    def __init__(self, frames: list[Image.Image]) -> None:
        self._frames = frames
        self.served = 0

    def grab(self) -> Image.Image:
        frame = self._frames[min(self.served, len(self._frames) - 1)]
        self.served += 1
        return frame


def test_a_wait_takes_a_screen_only_when_two_frames_in_a_row_show_it() -> None:
    menu, other = draw_main_menu(), draw_multiplayer()
    frames = _Frames([menu, other, other, menu, menu])
    fake = _fake("main_menu")
    seat = Seat(frames=frames, inputs=fake, clock=fake)

    found = actions.poll_for(seat, screen.main_menu, timeout=30)

    assert found is not None
    assert frames.served == 5


@pytest.mark.parametrize(
    "point",
    [
        ((INVITE_DECLINE[0] + INVITE_DECLINE[2]) // 2, 218),
    ],
)
def test_an_action_that_clicks_a_forbidden_input_fails_its_test(
    point: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The invitation's way back, pointed at DECLINE by mistake.
    monkeypatch.setattr(screen, "INVITE_BACK", point)
    fake = _fake("invitation")

    with pytest.raises(ForbiddenInput, match="decline"):
        actions.to_main_menu(fake.seat())


@pytest.mark.parametrize(
    "start",
    [
        "multiplayer",
        "settings",
        "invitation",
        "game_setup",
        "tribe",
        "multiplayer_after_accept",
    ],
)
def test_stale_frames_and_dropped_clicks_do_not_stop_it(start: str) -> None:
    fake = _fake(
        start,
        stale_frames=1,
        stale_until_pointer_leaves=True,
        drop_unhovered_clicks=True,
    )

    moved = actions.to_main_menu(fake.seat())

    assert moved is True
    assert fake.state == "main_menu"
    assert fake.dropped_clicks == 0


@pytest.mark.parametrize("start", sorted(seat_states()))
def test_every_click_lands_inside_the_button_it_is_meant_for(start: str) -> None:
    fake = _fake(start)

    try:
        actions.to_main_menu(fake.seat())
    except screen.Stop:
        pass

    assert fake.misses == []
    assert fake.hovering_grabs == 0


def test_the_pointer_is_on_the_button_before_each_click_and_off_it_before_a_frame() -> (
    None
):
    fake = _fake("multiplayer")

    actions.to_main_menu(fake.seat())

    at = fake.log.index(("click", 31, 31, "back"))
    assert fake.log[at - 1] == ("move", 31, 31)
    assert fake.log[at + 1] == ("move", *actions.PARK)


def test_a_wait_that_would_pass_the_deadline_stops_on_the_budget() -> None:
    fake = _fake("unknown")

    with pytest.raises(screen.Budget):
        actions.to_main_menu(fake.seat(deadline=fake.now() + 30))

    assert fake.elapsed < 60


def test_a_screen_that_never_arrives_stops_on_a_timeout() -> None:
    fake = FakeScreen(without_exits(seat_states(), "multiplayer"), "multiplayer")

    with pytest.raises(screen.Stop, match="timeout_main_menu"):
        actions.to_main_menu(fake.seat())


def test_nothing_is_clicked_once_the_deadline_has_passed() -> None:
    fake = _fake("multiplayer")

    with pytest.raises(screen.Budget):
        actions.press(fake.seat(deadline=fake.now() - 1), actions.BACK_ARROW)

    assert fake.log == []


def test_a_screen_already_showing_is_taken_though_the_deadline_passes() -> None:
    # The deadline falls between the two frames that confirm the main menu.
    fake = _fake("main_menu")
    seat = fake.seat(deadline=fake.now() + 0.5)

    assert actions.poll_for(seat, screen.main_menu, timeout=30) is not None


#: The screen layer's own frame, input, wait, and move functions, which call
#: the screen-grab and input tools and the system clock themselves.
DIRECT_TOOL_CALLS = {
    "grab", "xdo", "click", "park", "pull_refresh", "poll_for", "wait_for",
    "wait_frame", "budget", "recover", "to_list", "to_main_menu", "go_tab",
    "to_top", "restart_client", "reload_view", "list_check", "heal_list",
}  # fmt: skip


@pytest.mark.parametrize("module", ["actions", "commands", "capture", "resign"])
def test_code_above_the_seam_reaches_no_tool_or_clock_itself(module: str) -> None:
    source = (Path(screen.__file__).parent / f"{module}.py").read_text()
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"screen", "subprocess", "time"}
    }

    from_screen = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "scoretopia.seat.screen"
        for alias in node.names
    }

    from_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    clock_reads = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "datetime"
    } & {"now", "utcnow", "today"}

    assert "time" not in imported | from_modules
    assert clock_reads == set()
    assert from_screen & DIRECT_TOOL_CALLS == set()
    assert used & (DIRECT_TOOL_CALLS | {"run", "Popen", "check_output"}) == set()


def test_a_screen_first_seen_as_the_wait_runs_out_still_gets_its_second_frame() -> None:
    menu, other = draw_main_menu(), draw_multiplayer()
    frames = _Frames([other, other, other, menu, menu])
    fake = _fake("main_menu")
    seat = Seat(frames=frames, inputs=fake, clock=fake)

    # Looks come a second apart, so the fourth is just past the timeout.
    assert actions.poll_for(seat, screen.main_menu, timeout=2.5) is not None
