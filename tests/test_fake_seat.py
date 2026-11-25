"""The scripted fake screen behaves like the client it stands in for.

If the fake is wrong, every test built on it proves nothing, so its frames are
checked against the real screen tests and its faults are checked directly.
"""

from __future__ import annotations

import pytest
from fake_seat import (
    BACK_ARROW,
    FORBIDDEN_KINDS,
    INVITE_DECLINE,
    FakeScreen,
    ForbiddenInput,
    State,
    draw_unknown,
    seat_states,
)

from scoretopia.seat import screen

#: What the screen layer makes of each drawn state: the tests that must hold.
RECOGNISED_AS = {
    "main_menu": {"main_menu"},
    "multiplayer": {"mp_list"},
    "multiplayer_after_accept": {"mp_list"},
    "invitation": {"invite_panel"},
    "lobby": set(),
    "settings": {"settings_screen"},
    "game_setup": {"setup_screen"},
    # The live client's accept-side grid passes the create side's test too.
    "tribe": {"tribe_screen"},
    "accept_tribe": {"tribe_screen", "list_back_tribe"},
    "unknown": set(),
    "dialog": set(),
}
SCREEN_TESTS = (
    "main_menu",
    "mp_list",
    "invite_panel",
    "settings_screen",
    "setup_screen",
    "tribe_screen",
    "list_back_tribe",
)


@pytest.mark.parametrize("name", sorted(seat_states()))
def test_each_drawn_state_is_the_screen_it_stands_for_and_no_other(name: str) -> None:
    frame = seat_states()[name].frame

    seen = {test for test in SCREEN_TESTS if getattr(screen, test)(frame)}

    assert seen == RECOGNISED_AS[name]


def test_every_state_has_an_expectation() -> None:
    assert set(RECOGNISED_AS) == set(seat_states())


def test_a_click_on_a_button_moves_to_the_state_it_leads_to() -> None:
    fake = FakeScreen(seat_states(), "multiplayer")

    fake.click(31, 31)

    assert fake.state == "main_menu"
    assert screen.main_menu(fake.grab())
    assert fake.log == [("click", 31, 31, "back")]


def test_a_click_on_no_button_changes_nothing_and_is_listed_as_a_miss() -> None:
    fake = FakeScreen(seat_states(), "multiplayer")

    fake.click(900, 400)

    assert fake.state == "multiplayer"
    assert fake.misses == [("click", 900, 400, None)]


def test_stale_frames_show_the_old_screen_before_the_new_one() -> None:
    fake = FakeScreen(seat_states(), "multiplayer", stale_frames=2)

    fake.click(31, 31)
    seen = [screen.main_menu(fake.grab()) for _ in range(3)]

    assert seen == [False, False, True]


def test_a_frame_stays_stale_until_the_pointer_leaves_the_button() -> None:
    fake = FakeScreen(seat_states(), "multiplayer", stale_until_pointer_leaves=True)

    fake.click(31, 31)
    while_resting = [screen.main_menu(fake.grab()) for _ in range(5)]
    fake.move(BACK_ARROW[0] + 2, BACK_ARROW[1] + 2)
    still_on_it = screen.main_menu(fake.grab())
    fake.move(160, 240)

    assert while_resting == [False] * 5
    assert not still_on_it
    assert screen.main_menu(fake.grab())


def test_a_click_is_dropped_unless_the_pointer_was_moved_onto_the_button() -> None:
    fake = FakeScreen(seat_states(), "multiplayer", drop_unhovered_clicks=True)

    fake.click(31, 31)
    after_bare_click = fake.state
    fake.move(160, 240)
    fake.move(31, 31)
    fake.click(31, 31)

    assert after_bare_click == "multiplayer"
    assert fake.dropped_clicks == 1
    assert fake.state == "main_menu"


def test_a_key_moves_state_and_is_logged() -> None:
    fake = FakeScreen(seat_states(), "multiplayer")

    fake.key("Escape")

    assert fake.state == "settings"
    assert fake.keys == ["Escape"]


def test_the_clock_only_moves_when_slept() -> None:
    fake = FakeScreen(seat_states(), "main_menu")
    start = fake.now()

    fake.grab()
    fake.sleep(2.5)

    assert fake.now() - start == 2.5
    assert fake.elapsed == 2.5


@pytest.mark.parametrize("kind", FORBIDDEN_KINDS)
def test_a_click_on_a_forbidden_input_raises(kind: str) -> None:
    box = (100, 100, 200, 140)
    states = {"s": State("s", draw_unknown(), forbidden={kind: box})}
    fake = FakeScreen(states, "s")

    with pytest.raises(ForbiddenInput, match=kind):
        fake.click(150, 120)

    assert fake.log == [("click", 150, 120, kind)]


def test_the_scripted_screens_cover_every_forbidden_kind_but_profile() -> None:
    covered = {kind for state in seat_states().values() for kind in state.forbidden}

    assert covered == set(FORBIDDEN_KINDS) - {"profile"}


def test_declining_an_invitation_is_refused() -> None:
    fake = FakeScreen(seat_states(), "invitation")
    x = (INVITE_DECLINE[0] + INVITE_DECLINE[2]) // 2
    y = (INVITE_DECLINE[1] + INVITE_DECLINE[3]) // 2

    with pytest.raises(ForbiddenInput):
        fake.click(x, y)

    assert fake.state == "invitation"
