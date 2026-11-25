"""Returning the client to the main menu, against the scripted fake screen."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fake_seat import FakeScreen, seat_states

from scoretopia.seat import actions, screen


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


def test_escape_is_never_pressed_on_the_multiplayer_list() -> None:
    # There it opens Settings.
    fake = _fake("multiplayer")

    actions.to_main_menu(fake.seat())

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


def test_a_lobby_is_unknown_and_none_of_its_buttons_is_clicked() -> None:
    fake = _fake("lobby")

    with pytest.raises(screen.Stop, match="unknown_screen"):
        actions.to_main_menu(fake.seat())

    assert fake.clicks == []


@pytest.mark.parametrize(
    "start",
    ["multiplayer", "settings", "invitation", "tribe", "multiplayer_after_accept"],
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
    states = seat_states()
    stuck = states["multiplayer"]
    states["multiplayer"] = type(stuck)(
        "multiplayer", stuck.frame, buttons=stuck.buttons
    )
    fake = FakeScreen(states, "multiplayer")

    with pytest.raises(screen.Stop, match="timeout_main_menu"):
        actions.to_main_menu(fake.seat())


#: The screen layer's own frame, input, wait, and move functions, which call
#: the screen-grab and input tools and the system clock themselves.
DIRECT_TOOL_CALLS = {
    "grab", "xdo", "click", "park", "pull_refresh", "poll_for", "wait_for",
    "wait_frame", "budget", "recover", "to_list", "to_main_menu", "go_tab",
    "to_top", "restart_client", "reload_view", "list_check", "heal_list",
}  # fmt: skip


@pytest.mark.parametrize("module", ["actions", "commands"])
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

    assert "time" not in imported
    assert used & (DIRECT_TOOL_CALLS | {"run", "Popen", "check_output"}) == set()
