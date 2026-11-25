"""`scoretopia seat resign`, against the scripted fake screen."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fake_seat import (
    FakeScreen,
    OngoingGame,
    State,
    draw_unknown,
    ongoing_states,
)
from PIL import Image

from scoretopia import cli
from scoretopia.seat import commands, screen
from scoretopia.seat.seam import Seat

#: The buttons a resign presses on its way through one game, in order.
RESIGN_PATH = ["open", "turn0_ok", "game_settings", "resign", "confirm_ok", "done"]


def _turn(pattern: int, **how: object) -> OngoingGame:
    """A started game on the seat's turn."""
    return OngoingGame(pattern, seat_turn=True, **how)


def _capture_setups(games: list[OngoingGame], root: Path) -> None:
    """Capture the setup of each started game among these, as an earlier run."""
    fake = FakeScreen(ongoing_states(games), "main_menu")
    result = commands.capture_setup(fake.seat(), root / "captures", root / "frames")
    assert result.counts["captured"] == sum(game.kind == "started" for game in games)


def _fake(
    games: list[OngoingGame],
    start: str = "main_menu",
    *,
    states: dict[str, State] | None = None,
    **faults: object,
) -> FakeScreen:
    return FakeScreen(states or ongoing_states(games), start, **faults)


def _run(fake: FakeScreen, root: Path, **settings: float) -> dict:
    result = commands.resign(fake.seat(**settings), root / "captures", root / "frames")
    line = json.loads(result.line())
    line["exit_code"] = result.exit_code
    return line


def _pressed(fake: FakeScreen) -> list[str]:
    """The buttons clicked, in order."""
    return [click[3] for click in fake.clicks]


def _resigned_from(fake: FakeScreen) -> list[str]:
    """The cards whose game was resigned from, by index, in order."""
    return [
        name.split(":")[1].split("@")[0]
        for name in fake.visited
        if name.startswith("over:")
    ]


def test_a_game_on_the_seats_turn_with_a_captured_setup_is_resigned_from(
    tmp_path: Path,
) -> None:
    games = [_turn(0)]
    _capture_setups(games, tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert line == {
        "action": "resign",
        "outcome": "succeeded",
        "resigned": 1,
        "not_seats_turn": 0,
        "setup_missing": 0,
        "opponent_resigned": 0,
        "list_end": True,
        "budget_reached": False,
        "exit_code": 0,
    }
    assert _pressed(fake) == ["multiplayer", "card0", *RESIGN_PATH, "back"]
    assert fake.misses == []
    assert fake.state == "main_menu"


def test_a_game_on_the_seats_turn_without_a_captured_setup_is_not_touched(
    tmp_path: Path,
) -> None:
    fake = _fake([_turn(0)])

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["outcome"]) == (10, "nothing_to_do")
    assert (line["resigned"], line["setup_missing"]) == (0, 1)
    assert _pressed(fake) == ["multiplayer", "back"]


def test_a_game_where_it_is_not_the_seats_turn_is_not_touched(tmp_path: Path) -> None:
    games = [OngoingGame(0)]
    _capture_setups(games, tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["outcome"]) == (10, "nothing_to_do")
    assert (line["resigned"], line["not_seats_turn"]) == (0, 1)
    assert _pressed(fake) == ["multiplayer", "back"]


def test_only_the_games_to_resign_from_are_opened(tmp_path: Path) -> None:
    games = [
        OngoingGame(0),
        _turn(1),
        _turn(2),
        OngoingGame(3, "invitation"),
        OngoingGame(4, "lobby"),
    ]
    # An earlier run captured the first two; the third started since.
    _capture_setups(games[:2], tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert line["resigned"] == 1
    # The uncaptured game on the seat's turn, and the invitation drawn like it.
    assert line["setup_missing"] == 2
    # The game on another player's turn, and the lobby.
    assert line["not_seats_turn"] == 2
    assert [name for name in _pressed(fake) if name.startswith("card")] == ["card1"]
    assert _resigned_from(fake) == ["1"]
    assert fake.state == "main_menu"


def test_the_seat_resigns_and_sends_nothing_forbidden(tmp_path: Path) -> None:
    # The fake raises on a click in a forbidden box: Delete among the panel's
    # icons, a change in the game's Settings, the list's Profile icon.
    games = [_turn(0), _turn(1)]
    _capture_setups(games, tmp_path)
    states = ongoing_states(games)
    fake = _fake(games, states=states)

    line = _run(fake, tmp_path)

    assert line["resigned"] == 2
    crossed = {name.split(":")[0] for name in fake.visited}
    assert {"card", "gamesettings", "confirm"} <= crossed
    assert "delete" in states["card:0@0"].forbidden
    assert "settings" in states["gamesettings:0@0"].forbidden
    cards = {"card0", "card1"}
    assert set(_pressed(fake)) == {"multiplayer", *cards, *RESIGN_PATH, "back"}
    assert fake.misses == []


def test_a_game_the_other_player_resigned_is_closed_and_counted(
    tmp_path: Path,
) -> None:
    games = [_turn(0, opponent_resigned=True)]
    _capture_setups(games, tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["resigned"]) == (10, 0)
    assert line["opponent_resigned"] == 1
    assert _pressed(fake) == ["multiplayer", "card0", "panel_back", "back"]


@pytest.mark.parametrize("turn0", ["created", "accepted", None])
def test_the_turn_0_dialog_is_closed_wherever_it_shows_and_not_needed(
    turn0: str | None, tmp_path: Path
) -> None:
    games = [_turn(0, turn0=turn0)]
    _capture_setups(games, tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["resigned"]) == (0, 1)
    assert ("turn0_ok" in _pressed(fake)) == (turn0 is not None)
    assert fake.misses == []


def test_more_games_than_fit_are_each_resigned_from_once(tmp_path: Path) -> None:
    # Seven cards, and after each resign the list comes back at its end, with
    # its top cards out of sight.
    games = [_turn(n) for n in range(7)]
    _capture_setups(games, tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["resigned"], line["list_end"]) == (7, True)
    assert sorted(_resigned_from(fake)) == [str(n) for n in range(7)]
    assert fake.misses == []
    assert fake.hovering_grabs == 0


def test_the_end_screens_done_may_lead_to_the_main_menu(tmp_path: Path) -> None:
    games = [_turn(0), _turn(1)]
    _capture_setups(games, tmp_path)
    fake = _fake(games, states=ongoing_states(games, resigned_to="main_menu"))

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["resigned"]) == (0, 2)
    assert sorted(_resigned_from(fake)) == ["0", "1"]


def _with_unknown_after(games: list[OngoingGame], prefix: str) -> dict[str, State]:
    """The states, where every state named ``<prefix>:...`` is an unknown screen."""
    states = ongoing_states(games)
    for name in list(states):
        if name.startswith(prefix + ":"):
            states[name] = State(name, draw_unknown())
    return states


@pytest.mark.parametrize(
    ("stage", "code"),
    [
        ("card", "timeout_card_panel"),
        ("gamesettings", "timeout_resign_ready"),
        ("confirm", "timeout_resign_confirm"),
        ("over", "timeout_resigned_over"),
    ],
)
def test_an_unknown_screen_saves_its_frame_and_exits_with_its_code(
    stage: str, code: str, tmp_path: Path
) -> None:
    games = [_turn(0), _turn(1)]
    _capture_setups(games, tmp_path)
    fake = _fake(games, states=_with_unknown_after(games, stage))

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["outcome"]) == (20, "unknown_screen")
    assert (line["code"], line["resigned"]) == (code, 0)
    (saved,) = (tmp_path / "frames").iterdir()
    assert saved.name == line["frame"]
    with Image.open(saved) as im:
        assert screen.screen_name(im.convert("RGB")) == "unknown"
    # The second game is not begun after the stop.
    assert len([name for name in _pressed(fake) if name == "open"]) <= 1


def test_a_resign_counts_once_the_end_screen_shows_whatever_follows(
    tmp_path: Path,
) -> None:
    games = [_turn(0)]
    _capture_setups(games, tmp_path)
    states = ongoing_states(games, resigned_to="unknown")
    fake = _fake(games, states=states)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["code"]) == (20, "timeout_resigned_done")
    assert line["resigned"] == 1


def test_stale_frames_and_dropped_clicks_do_not_stop_it(tmp_path: Path) -> None:
    games = [_turn(0), _turn(1)]
    _capture_setups(games, tmp_path)
    fake = _fake(
        games,
        stale_frames=1,
        stale_until_pointer_leaves=True,
        drop_unhovered_clicks=True,
    )

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["resigned"]) == (0, 2)
    assert fake.dropped_clicks == 0


def test_no_resign_starts_without_the_time_to_finish_it(tmp_path: Path) -> None:
    games = [_turn(0)]
    _capture_setups(games, tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path, deadline=fake.now() + 200)

    assert line["budget_reached"] is True
    assert (line["exit_code"], line["code"], line["resigned"]) == (1, "budget", 0)
    assert _pressed(fake) == ["multiplayer", "back"]
    assert fake.state == "main_menu"


def test_an_unreadable_record_fails_with_nothing_clicked(tmp_path: Path) -> None:
    (tmp_path / "captures").mkdir()
    (tmp_path / "captures" / "record.json").write_text("{not json")
    fake = _fake([_turn(0)])

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["code"]) == (1, "record_unreadable")
    assert fake.clicks == []


def test_the_command_prints_how_many_games_were_resigned_from_on_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    games = [_turn(0)]
    _capture_setups(games, tmp_path)
    fake = _fake(games)

    @contextmanager
    def fake_live_seat(**_: object) -> Iterator[Seat]:
        yield fake.seat()

    monkeypatch.setattr(cli, "live_seat", fake_live_seat)
    config = tmp_path / "seat.yaml"
    config.write_text("captures:\n  path: captures\nframes:\n  path: frames\n")

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["seat", "resign", "--config", str(config)])

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {
        "action": "resign",
        "outcome": "succeeded",
        "resigned": 1,
        "not_seats_turn": 0,
        "setup_missing": 0,
        "opponent_resigned": 0,
        "list_end": True,
        "budget_reached": False,
    }
