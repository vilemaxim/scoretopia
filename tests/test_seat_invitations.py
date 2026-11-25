"""`scoretopia seat accept-invitations`, against the scripted fake screen.

The fake's frames carry no text, so the names an invitation shows come from a
stand-in for the OCR, keyed by the card that is open. Every name here is
invented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from fake_seat import (
    CREATE_TRIBE_1,
    FakeScreen,
    OngoingGame,
    State,
    draw_tribe,
    draw_unknown,
    ongoing_states,
)
from PIL import Image

from scoretopia import cli
from scoretopia.orders import Orders, write_orders
from scoretopia.seat import commands, invitations, screen
from scoretopia.seat.seam import Seat

SEAT = "Scorekeeper"
KNOWN = ("Mossback", "Tin Otter", "Quill91")


def _invitations(count: int) -> list[OngoingGame]:
    return [OngoingGame(pattern, "invitation") for pattern in range(count)]


def _fake(
    games: list[OngoingGame],
    start: str = "main_menu",
    *,
    states: dict[str, State] | None = None,
    **faults: object,
) -> FakeScreen:
    return FakeScreen(states or ongoing_states(games), start, **faults)


def _run(
    fake: FakeScreen,
    root: Path,
    shown: Sequence[Sequence[str]],
    *,
    known: Sequence[str] | None = KNOWN,
    seat_name: str | None = SEAT,
    **settings: float,
) -> dict:
    """Run the command; ``shown[i]`` is the names card ``i``'s panel shows."""
    orders = root / "orders.json"
    if known is not None:
        write_orders(orders, Orders(known_players=tuple(known)))

    def read_names(frame: Image.Image) -> Sequence[str]:
        return shown[int(fake.state.split(":")[1].split("@")[0])]

    result = commands.accept_invitations(
        fake.seat(**settings), orders, seat_name, root / "frames", read_names
    )
    line = json.loads(result.line())
    line["exit_code"] = result.exit_code
    return line


def _accepted(fake: FakeScreen) -> list[str]:
    """The cards whose invitation was accepted, by index, in order."""
    return [
        name.split(":")[1].split("@")[0]
        for name in fake.visited
        if name.startswith("joined:")
    ]


def test_an_invitation_from_a_known_player_is_accepted(tmp_path: Path) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Mossback", SEAT]])

    assert line == {
        "action": "accept-invitations",
        "outcome": "succeeded",
        "accepted": 1,
        "unanswered": 0,
        "unread": 0,
        "list_end": True,
        "budget_reached": False,
        "exit_code": 0,
    }
    assert _accepted(fake) == ["0"]
    assert fake.misses == []
    assert fake.state == "main_menu"


def test_an_invitation_from_someone_not_known_is_left_and_never_declined(
    tmp_path: Path,
) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Nobody Yet", SEAT]])

    assert (line["exit_code"], line["outcome"]) == (10, "nothing_to_do")
    assert (line["accepted"], line["unanswered"]) == (0, 1)
    # Decline raises in the fake; the panel was closed by BACK and nothing else.
    assert [click[3] for click in fake.clicks if click[3] != "multiplayer"] == [
        "card0",
        "back",
        "back",
    ]
    assert _accepted(fake) == []
    assert fake.state == "main_menu"


@pytest.mark.parametrize(
    ("known", "read"),
    [("Quill91", "Qui1l9l"), ("Tin Otter", "Tin 0tter"), ("Mossback", "Mossback.")],
)
def test_a_name_read_with_a_digit_as_a_letter_still_matches(
    known: str, read: str, tmp_path: Path
) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [[read, SEAT]], known=[known])

    assert (line["accepted"], line["unanswered"]) == (1, 0)


def test_a_name_matching_two_known_players_is_not_known(tmp_path: Path) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Molly", SEAT]], known=["Molly", "Mol1y"])

    assert (line["accepted"], line["unanswered"]) == (0, 1)
    assert _accepted(fake) == []


def test_in_a_game_of_three_one_player_not_known_means_it_is_left(
    tmp_path: Path,
) -> None:
    fake = _fake(_invitations(2))

    line = _run(
        fake,
        tmp_path,
        [["Mossback", "Nobody Yet", SEAT], ["Mossback", SEAT, "Tin Otter"]],
    )

    assert (line["accepted"], line["unanswered"]) == (1, 1)
    assert _accepted(fake) == ["1"]


def test_an_ai_opponent_is_not_a_player_to_know(tmp_path: Path) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Mossback", SEAT, "Crazy Bot"]])

    assert line["accepted"] == 1


def test_with_no_orders_file_nothing_is_accepted(tmp_path: Path) -> None:
    fake = _fake(_invitations(2))

    line = _run(fake, tmp_path, [["Mossback", SEAT]] * 2, known=None)

    assert (line["exit_code"], line["accepted"], line["unanswered"]) == (10, 0, 2)
    assert _accepted(fake) == []


def test_with_an_orders_file_that_cannot_be_read_nothing_is_accepted(
    tmp_path: Path,
) -> None:
    (tmp_path / "orders.json").write_text("{not json")
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Mossback", SEAT]], known=None)

    assert (line["exit_code"], line["accepted"], line["unanswered"]) == (10, 0, 1)


def test_orders_of_a_version_it_does_not_know_stop_it_before_any_click(
    tmp_path: Path,
) -> None:
    (tmp_path / "orders.json").write_text('{"version": 99, "known_players": []}')
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Mossback", SEAT]], known=None)

    assert (line["exit_code"], line["code"]) == (1, "orders_version")
    assert fake.log == []


def test_the_orders_are_read_once_at_the_start(tmp_path: Path) -> None:
    fake = _fake(_invitations(2))
    orders = tmp_path / "orders.json"
    write_orders(orders, Orders(known_players=KNOWN))

    def read_names(frame: Image.Image) -> Sequence[str]:
        # Recording rewrites the file while the run is going.
        write_orders(orders, Orders())
        return ["Mossback", SEAT]

    result = commands.accept_invitations(
        fake.seat(), orders, SEAT, tmp_path / "frames", read_names
    )

    assert json.loads(result.line())["accepted"] == 2


def test_without_the_seats_own_name_nothing_is_accepted(tmp_path: Path) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Mossback", SEAT]], seat_name=None)

    assert (line["accepted"], line["unanswered"]) == (0, 1)


@pytest.mark.parametrize(
    "shown",
    [["Mossback"], [SEAT], [], ["Mossback", SEAT, SEAT]],
    ids=["seat_not_read", "no_one_else", "nothing_read", "seat_twice"],
)
def test_a_panel_that_does_not_show_the_seat_and_someone_else_is_left(
    shown: list[str], tmp_path: Path
) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [shown])

    assert (line["accepted"], line["unanswered"]) == (0, 1)


def test_a_panel_the_reader_fails_on_is_left(tmp_path: Path) -> None:
    fake = _fake(_invitations(1))
    orders = tmp_path / "orders.json"
    write_orders(orders, Orders(known_players=KNOWN))

    def read_names(frame: Image.Image) -> Sequence[str]:
        raise RuntimeError("no model")

    result = commands.accept_invitations(
        fake.seat(), orders, SEAT, tmp_path / "frames", read_names
    )

    line = json.loads(result.line())
    assert (line["accepted"], line["unanswered"], line["unread"]) == (0, 1, 1)
    # A reader that does not work is a failed run, not a list of strangers.
    assert (result.exit_code, line["code"]) == (1, "names_unread")
    assert "no model" not in result.line()
    assert fake.state == "main_menu"


def test_the_names_are_read_from_the_invitations_own_panel(tmp_path: Path) -> None:
    fake = _fake(_invitations(1))
    orders = tmp_path / "orders.json"
    write_orders(orders, Orders(known_players=KNOWN))
    read: list[bool] = []

    def read_names(frame: Image.Image) -> Sequence[str]:
        read.append(bool(screen.invite_panel(frame)))
        return ["Mossback", SEAT]

    commands.accept_invitations(
        fake.seat(), orders, SEAT, tmp_path / "frames", read_names
    )

    assert read == [True]


def test_the_result_line_carries_the_two_counts_and_no_names(tmp_path: Path) -> None:
    fake = _fake(_invitations(2))
    shown = [["Mossback", SEAT], ["Nobody Yet", SEAT]]
    orders = tmp_path / "orders.json"
    write_orders(orders, Orders(known_players=KNOWN))

    result = commands.accept_invitations(
        fake.seat(),
        orders,
        SEAT,
        tmp_path / "frames",
        lambda frame: shown[int(fake.state.split(":")[1].split("@")[0])],
    )

    text = result.line()
    assert json.loads(text) == {
        "action": "accept-invitations",
        "outcome": "succeeded",
        "accepted": 1,
        "unanswered": 1,
        "unread": 0,
        "list_end": True,
        "budget_reached": False,
    }
    for name in (*KNOWN, SEAT, "Nobody Yet"):
        assert name.lower() not in text.lower()


def test_cards_that_are_not_invitations_are_closed_and_not_counted(
    tmp_path: Path,
) -> None:
    games = [
        OngoingGame(0, "invitation"),
        OngoingGame(1, "started"),
        OngoingGame(2, "lobby"),
    ]
    fake = _fake(games)

    line = _run(fake, tmp_path, [["Mossback", SEAT], [], []])

    assert (line["accepted"], line["unanswered"]) == (1, 0)
    buttons = [click[3] for click in fake.clicks]
    assert "open" not in buttons
    assert "more_info" not in buttons
    assert fake.misses == []
    assert fake.hovering_grabs == 0
    assert fake.state == "main_menu"


def test_a_list_with_no_invitation_is_nothing_to_do(tmp_path: Path) -> None:
    fake = _fake([OngoingGame(0), OngoingGame(1, "lobby")])

    line = _run(fake, tmp_path, [[], []])

    assert (line["exit_code"], line["accepted"], line["unanswered"]) == (10, 0, 0)


def test_after_an_accept_the_reopened_tribe_screen_is_left_for_the_menu(
    tmp_path: Path,
) -> None:
    fake = _fake(_invitations(1))

    _run(fake, tmp_path, [["Mossback", SEAT]])

    # The list's back arrow reopens the tribe screen once an invitation has
    # been accepted; the run still ends on the main menu.
    assert "reopened_tribe" in fake.visited
    assert fake.state == "main_menu"


def test_the_tribe_is_picked_where_the_grid_is_when_it_sits_lower(
    tmp_path: Path,
) -> None:
    # PICK YOUR TRIBE after ACCEPT has been seen with the grid at both heights.
    games = _invitations(1)
    states = ongoing_states(games)
    for name in list(states):
        if name.startswith("accepttribe:"):
            states[name] = State(
                name,
                draw_tribe(),
                buttons={"tribe1": CREATE_TRIBE_1},
                on_click={"tribe1": name.replace("accepttribe", "pick")},
            )
    fake = _fake(games, states=states)

    line = _run(fake, tmp_path, [["Mossback", SEAT]])

    assert line["accepted"] == 1
    assert fake.misses == []


def test_stale_frames_and_dropped_clicks_do_not_stop_it(tmp_path: Path) -> None:
    fake = _fake(
        _invitations(2),
        stale_frames=1,
        stale_until_pointer_leaves=True,
        drop_unhovered_clicks=True,
    )

    line = _run(fake, tmp_path, [["Mossback", SEAT], ["Nobody Yet", SEAT]])

    assert (line["exit_code"], line["accepted"], line["unanswered"]) == (0, 1, 1)
    assert fake.dropped_clicks == 0


def test_an_unknown_screen_after_accept_saves_its_frame_and_counts_nothing(
    tmp_path: Path,
) -> None:
    games = _invitations(1)
    states = ongoing_states(games)
    for name in list(states):
        if name.startswith("accepttribe:"):
            states[name] = State(name, draw_unknown())
    fake = _fake(games, states=states)

    line = _run(fake, tmp_path, [["Mossback", SEAT]])

    assert (line["exit_code"], line["outcome"]) == (20, "unknown_screen")
    assert line["code"] == "timeout_accept_tribe_screen"
    assert (line["accepted"], line["unanswered"]) == (0, 0)
    assert [p.name for p in (tmp_path / "frames").iterdir()] == [line["frame"]]


def test_an_accept_counts_even_when_the_way_back_stops(tmp_path: Path) -> None:
    games = _invitations(1)
    states = ongoing_states(games)
    joined = states["joined:0@0"]
    states["joined:0@0"] = State(joined.name, joined.frame, forbidden=joined.forbidden)
    fake = _fake(games, start="ongoing@0", states=states)

    line = _run(fake, tmp_path, [["Mossback", SEAT]])

    assert (line["exit_code"], line["code"]) == (20, "timeout_panel_closed")
    assert line["accepted"] == 1


def test_a_lobby_the_seat_could_start_stops_it_with_nothing_pressed(
    tmp_path: Path,
) -> None:
    fake = _fake([OngoingGame(0, "own_lobby")], start="ongoing@0")

    line = _run(fake, tmp_path, [[]])

    assert (line["exit_code"], line["code"]) == (20, "card_panel_without_open")
    assert [click[3] for click in fake.clicks] == ["card0"]


def test_no_invitation_is_opened_without_the_time_to_answer_it(tmp_path: Path) -> None:
    fake = _fake(_invitations(1))

    line = _run(fake, tmp_path, [["Mossback", SEAT]], deadline=fake.now() + 150)

    assert line["budget_reached"] is True
    assert (line["exit_code"], line["code"], line["accepted"]) == (1, "budget", 0)
    assert not any(name.startswith("card:") for name in fake.visited)
    assert fake.state == "main_menu"


def test_the_command_reads_its_orders_and_the_seats_name_from_its_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _fake(_invitations(1))

    @contextmanager
    def fake_live_seat(**_: object) -> Iterator[Seat]:
        yield fake.seat()

    monkeypatch.setattr(cli, "live_seat", fake_live_seat)
    monkeypatch.setattr(invitations, "ocr_names", lambda frame: ["Mossback", SEAT])
    write_orders(tmp_path / "shared" / "orders.json", Orders(known_players=KNOWN))
    config = tmp_path / "seat.yaml"
    config.write_text(
        "frames:\n  path: frames\norders:\n  path: shared/orders.json\n"
        f"seat_name: {SEAT}\n"
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["seat", "accept-invitations", "--config", str(config)])

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["accepted"] == 1


_SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "screenshots"
_INVITATION_SAMPLES = sorted(_SAMPLES.glob("*-invited-invite.png"))


@pytest.mark.skipif(not _INVITATION_SAMPLES, reason="no local invitation sample")
@pytest.mark.parametrize("sample", _INVITATION_SAMPLES, ids=lambda path: path.stem)
def test_the_ocr_reads_every_name_on_a_recorded_invitation(sample: Path) -> None:
    # The sample and its golden stay local: they show real names.
    golden = json.loads(sample.with_suffix(".json").read_text())
    expected = [player["name"] for player in golden["players"]]
    with Image.open(sample) as opened:
        frame = opened.convert("RGB")

    names = invitations.ocr_names(frame)

    assert len(names) == len(expected)
    # Each golden name in turn as the seat, the rest as the known players.
    for seat_name in expected:
        others = [name for name in expected if name != seat_name]
        assert invitations.all_known(names, others, seat_name)
        assert not invitations.all_known(names, [], seat_name)
