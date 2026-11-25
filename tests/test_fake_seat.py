"""The scripted fake screen behaves like the client it stands in for.

If the fake is wrong, every test built on it proves nothing, so its frames are
checked against the real screen tests and its faults are checked directly.
"""

from __future__ import annotations

import pytest
from fake_seat import (
    AFTER_ACCEPT,
    BACK_ARROW,
    FORBIDDEN_KINDS,
    INVITE_ACCEPT,
    INVITE_DECLINE,
    FakeScreen,
    FinishedGame,
    ForbiddenInput,
    OngoingGame,
    State,
    draw_card_info,
    draw_card_panel,
    draw_game_settings,
    draw_game_view,
    draw_invitation,
    draw_invited_lobby,
    draw_joined_lobby,
    draw_ongoing,
    draw_replays,
    draw_tribe_pick,
    draw_unknown,
    ongoing_states,
    replay_states,
    scroll_offsets,
    seat_states,
)
from PIL import Image

from scoretopia.seat import panels, screen

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
    "profile": set(),
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


def test_a_click_is_dropped_unless_the_pointer_rested_on_the_button_first() -> None:
    fake = FakeScreen(seat_states(), "multiplayer", drop_unhovered_clicks=True)

    fake.click(31, 31)
    fake.click(31, 31)
    after_bare_clicks = fake.state
    fake.move(31, 31)
    fake.click(31, 31)
    after_a_move_with_no_pause = fake.state
    fake.move(31, 31)
    fake.sleep(0.5)
    fake.click(31, 31)

    assert after_bare_clicks == "multiplayer"
    assert after_a_move_with_no_pause == "multiplayer"
    assert fake.dropped_clicks == 3
    assert fake.state == "main_menu"


def test_frames_taken_with_the_pointer_on_a_button_are_counted() -> None:
    fake = FakeScreen(seat_states(), "multiplayer")

    fake.grab()
    fake.move(31, 31)
    fake.grab()
    fake.move(160, 240)
    fake.grab()

    assert fake.hovering_grabs == 1


def test_a_state_refuses_a_forbidden_kind_it_does_not_know() -> None:
    with pytest.raises(ValueError, match="not a forbidden kind"):
        State("s", draw_unknown(), forbidden={"mystery": (0, 0, 1, 1)})


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


def test_the_scripted_screens_cover_every_forbidden_kind() -> None:
    covered = {kind for state in seat_states().values() for kind in state.forbidden}

    assert covered == set(FORBIDDEN_KINDS)


def test_declining_an_invitation_is_refused() -> None:
    fake = FakeScreen(seat_states(), "invitation")
    x = (INVITE_DECLINE[0] + INVITE_DECLINE[2]) // 2
    y = (INVITE_DECLINE[1] + INVITE_DECLINE[3]) // 2

    with pytest.raises(ForbiddenInput):
        fake.click(x, y)

    assert fake.state == "invitation"


# ------------------------------------------------------------ the Replays tab


def test_a_state_changes_by_itself_when_its_time_is_up() -> None:
    states = replay_states([FinishedGame(0, plays_for=40)], loads_for=8)
    fake = FakeScreen(states, "loading:0@0")

    fake.sleep(7)
    fake.grab()
    still_loading = fake.state
    fake.sleep(2)
    playing = screen.exit_blue(fake.grab())
    fake.sleep(39)
    fake.grab()

    assert still_loading == "loading:0@0"
    assert playing
    assert fake.state == "finish:0@0"
    assert screen.finish_green(fake.grab())


def test_the_wheel_moves_a_long_list_and_stops_at_its_ends() -> None:
    fake = FakeScreen(replay_states([FinishedGame(n) for n in range(5)]), "replays@0")

    fake.scroll(640, 400, -3)
    at_top = fake.state
    for _ in range(4):
        fake.scroll(640, 400, 3)

    assert at_top == "replays@0"
    assert fake.state == "replays@100"
    assert fake.log[0] == ("scroll", 640, 400, -3)


def test_a_short_list_does_not_scroll() -> None:
    assert scroll_offsets(4) == [0]
    assert scroll_offsets(5) == [0, 50, 100]


@pytest.mark.parametrize("offset", scroll_offsets(5))
def test_the_drawn_replays_tab_is_the_list_with_its_whole_rows(offset: int) -> None:
    games = [FinishedGame(n) for n in range(5)]
    frame = draw_replays(games, offset)

    rows = screen.bands(frame)

    assert screen.mp_list(frame)
    assert screen.selected_tab(frame) == "replays"
    assert all(bottom - top == 60 for top, bottom in rows)
    keys = [screen.key_of(frame, row, "result") for row in rows]
    assert None not in keys
    assert len(set(keys)) == len(keys)


def test_a_row_keeps_its_key_wherever_the_list_is_scrolled() -> None:
    games = [FinishedGame(n) for n in range(5)]
    middle = draw_replays(games, 50)
    end = draw_replays(games, 100)

    keys_in_the_middle = [
        screen.key_of(middle, row, "result") for row in screen.bands(middle)
    ]
    keys_at_the_end = [screen.key_of(end, row, "result") for row in screen.bands(end)]

    assert keys_in_the_middle[1:] == keys_at_the_end


def test_the_profile_icon_on_the_list_is_forbidden() -> None:
    # A replay's Exit and Finish Game sit at the same point.
    fake = FakeScreen(replay_states([FinishedGame(0)]), "replays@0")

    with pytest.raises(ForbiddenInput, match="profile"):
        fake.click(765, 665)


def test_the_panels_other_icons_are_forbidden() -> None:
    fake = FakeScreen(replay_states([FinishedGame(0)]), "panel:0@0")

    with pytest.raises(ForbiddenInput, match="delete"):
        fake.click(560, 250)


# ------------------------------------------------------------ the Ongoing tab


@pytest.mark.parametrize("offset", scroll_offsets(5))
def test_the_drawn_ongoing_tab_is_the_list_with_its_whole_cards(offset: int) -> None:
    games = [OngoingGame(n) for n in range(5)]
    frame = draw_ongoing(games, offset)

    cards = screen.bands(frame)

    assert screen.mp_list(frame)
    assert screen.selected_tab(frame) == "ongoing"
    assert all(bottom - top == 60 for top, bottom in cards)
    keys = [screen.key_of(frame, card, "setup") for card in cards]
    assert None not in keys
    assert len(set(keys)) == len(keys)


def test_no_two_drawn_cards_are_within_the_matching_tolerance() -> None:
    games = [OngoingGame(n) for n in range(10)]
    masks = []
    for start in (0, 4, 8):
        frame = draw_ongoing(games[start : start + 4], 0)
        masks += [screen.mask_of(frame, card, "setup") for card in screen.bands(frame)]

    assert len(masks) == 10
    for at, mask in enumerate(masks):
        assert screen.match(mask, masks[:at] + masks[at + 1 :]) is None


def test_only_a_started_games_panel_has_open_and_nothing_wider() -> None:
    started = draw_card_panel(OngoingGame(0))
    joined = draw_joined_lobby(OngoingGame(0))
    top = screen.panel_top(started)

    open_y = screen.find_blue(started, 760, top + 250, top + 420)

    assert open_y is not None
    assert not screen.blue(screen.px(started, 705, open_y))
    assert not screen.invite_panel(started)
    assert screen.find_blue(joined, 760, top + 250, top + 420) is None
    assert screen.find_blue(joined, 640, top + 250, top + 420) is not None


def test_a_cards_game_info_shows_its_back_over_the_panel() -> None:
    panel = draw_card_panel(OngoingGame(0))
    info = draw_card_info(OngoingGame(0))
    top = screen.panel_top(info)

    assert top == screen.panel_top(panel)
    assert screen.find_blue(panel, 640, top + 200, top + 300) is None
    assert screen.find_blue(info, 640, top + 200, top + 300) is not None


def test_the_icons_beside_more_info_on_a_cards_panel_are_forbidden() -> None:
    fake = FakeScreen(ongoing_states([OngoingGame(0)]), "card:0@0")

    with pytest.raises(ForbiddenInput, match="delete"):
        fake.click(700, 250)


# ------------------------------------------------------------ accepting


def test_the_drawn_invitation_reads_as_one_with_accept_found_beside_its_label() -> None:
    frame = draw_invitation()

    panel = panels.read_panel(frame)

    assert panel.kind == panels.INVITATION
    assert INVITE_ACCEPT[0] <= panels.ACCEPT_X < INVITE_ACCEPT[2]
    assert INVITE_ACCEPT[1] <= panel.y < INVITE_ACCEPT[3]
    # At its centre the label breaks the button's blue, as on the live client.
    top = screen.panel_top(frame)
    assert panels.button_y(frame, panels.ACCEPT_X, top) is None
    assert panel.y is not None


def test_the_drawn_tribe_panel_shows_pick() -> None:
    assert screen.pick_ready(draw_tribe_pick())


def test_the_drawn_lobby_after_an_accept_is_a_joined_lobby() -> None:
    frame = draw_invited_lobby(OngoingGame(0))

    assert panels.joined_lobby(frame)
    assert not screen.invite_panel(frame)
    assert not screen.mp_list(frame)


def test_leave_on_a_joined_lobby_is_forbidden() -> None:
    fake = FakeScreen(ongoing_states([OngoingGame(0, "invitation")]), "joined:0@0")

    with pytest.raises(ForbiddenInput, match="delete"):
        fake.click(760, 200)


def test_after_an_accept_the_lists_back_arrow_reopens_the_tribe_screen() -> None:
    states = ongoing_states([OngoingGame(0, "invitation")])
    fake = FakeScreen(states, "joined:0@0")

    fake.click(640, 516)
    on_list = fake.state
    fake.click(31, 31)
    reopened = screen.list_back_tribe(fake.grab())
    fake.click(31, 31)
    fake.click(31, 31)

    assert on_list == "ongoing@0" + AFTER_ACCEPT
    assert reopened
    assert fake.visited[-3:] == ["reopened_tribe", "ongoing@0", "main_menu"]


# ------------------------------------------------------------ resigning


def test_only_a_card_drawn_blue_reads_as_on_the_seats_turn() -> None:
    games = [
        OngoingGame(0, seat_turn=True),
        OngoingGame(1),
        OngoingGame(2, "invitation"),
        OngoingGame(3, "lobby"),
    ]
    frame = draw_ongoing(games, 0)

    cards = screen.bands(frame)

    assert screen.mp_list(frame)
    assert [screen.bot_turn_card(frame, card) for card in cards] == [
        True,
        False,
        True,
        False,
    ]


def test_a_card_keeps_its_mask_when_the_turn_passes() -> None:
    theirs = draw_ongoing([OngoingGame(0)], 0)
    ours = draw_ongoing([OngoingGame(0, seat_turn=True)], 0)
    (card,) = screen.bands(ours)

    assert screen.mask_of(ours, card) == screen.mask_of(theirs, card)


def test_a_panel_on_the_seats_turn_is_still_a_started_games() -> None:
    panel = draw_card_panel(OngoingGame(0, seat_turn=True))

    assert panels.read_panel(panel).kind == panels.STARTED
    assert not screen.resigned_panel(panel)


def test_only_the_panel_of_a_game_the_opponent_resigned_shows_the_robot() -> None:
    game = OngoingGame(0, seat_turn=True, opponent_resigned=True)

    assert screen.resigned_panel(draw_card_panel(game))
    assert panels.read_panel(draw_card_panel(game)).kind == panels.STARTED


GAME_TESTS = ("resign_ready", "resign_confirm", "game_over")


@pytest.mark.parametrize(
    ("frame", "holds", "turn0"),
    [
        (draw_game_view(), set(), None),
        (draw_game_view("created"), set(), screen.TURN0_OK[0]),
        (draw_game_view("accepted"), set(), screen.TURN0_OK[1]),
        (draw_game_settings(), {"resign_ready"}, None),
        (draw_game_settings(confirm=True), {"resign_confirm"}, None),
    ],
    ids=["game", "turn0_created", "turn0_accepted", "settings", "confirm"],
)
def test_each_drawn_game_screen_is_the_screen_it_stands_for(
    frame: Image.Image, holds: set[str], turn0: tuple[int, int] | None
) -> None:
    assert {name for name in GAME_TESTS if getattr(screen, name)(frame)} == holds
    assert screen.turn0_ok(frame) == turn0
    assert not screen.on_multiplayer(frame)
    assert not screen.main_menu(frame)
    assert not screen.finish_green(frame) and not screen.exit_blue(frame)


def test_everything_in_game_settings_but_its_buttons_is_forbidden() -> None:
    states = ongoing_states([OngoingGame(0, seat_turn=True)])
    fake = FakeScreen(states, "gamesettings:0@0")

    with pytest.raises(ForbiddenInput, match="settings"):
        fake.click(640, 300)


def test_resigning_in_the_game_leads_to_its_end_screen_and_back_to_the_list() -> None:
    fake = FakeScreen(ongoing_states([OngoingGame(0, seat_turn=True)]), "card:0@0")

    for point in [(760, 516), None, (640, 457), (515, 665), (1225, 30), (777, 404)]:
        if point is None:
            fake.sleep(8)
            fake.grab()
        else:
            fake.click(*point)

    assert fake.state == "over:0@0"
    assert screen.game_over(fake.grab())
    fake.click(*screen.done_point(fake.grab()))
    assert fake.state == "ongoing@0"
    assert fake.misses == []
