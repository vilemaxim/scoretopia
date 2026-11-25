"""`scoretopia seat main-menu`: its result line, exit code, and saved frame."""

from __future__ import annotations

import json
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fake_seat import FakeScreen, ForbiddenInput, seat_states, without_exits
from PIL import Image

from scoretopia import cli
from scoretopia.seat import actions, commands, screen
from scoretopia.seat.seam import Seat


def _fake(start: str) -> FakeScreen:
    return FakeScreen(seat_states(), start)


def test_returning_to_the_main_menu_succeeds(tmp_path: Path) -> None:
    fake = _fake("multiplayer")

    result = commands.main_menu(fake.seat(), tmp_path)

    assert result.exit_code == 0
    assert json.loads(result.line()) == {
        "action": "main-menu",
        "outcome": "succeeded",
    }
    assert fake.state == "main_menu"


def test_it_still_succeeds_when_frames_are_stale_and_clicks_are_dropped(
    tmp_path: Path,
) -> None:
    fake = FakeScreen(
        seat_states(),
        "invitation",
        stale_frames=2,
        stale_until_pointer_leaves=True,
        drop_unhovered_clicks=True,
    )

    result = commands.main_menu(fake.seat(), tmp_path)

    assert result.exit_code == 0
    assert fake.state == "main_menu"
    assert fake.misses == []


def test_a_forbidden_input_is_not_reported_as_an_ordinary_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screen, "INVITE_BACK", (760, 218))

    with pytest.raises(ForbiddenInput):
        commands.main_menu(_fake("invitation").seat(), tmp_path)


def test_the_saved_frame_is_the_one_judged_unknown_not_a_later_one(
    tmp_path: Path,
) -> None:
    alone, through_command = _fake("unknown"), _fake("unknown")
    with pytest.raises(actions.UnknownScreen):
        actions.to_main_menu(alone.seat())

    commands.main_menu(through_command.seat(), tmp_path)

    assert through_command.frames_served == alone.frames_served


def test_already_on_the_main_menu_is_nothing_to_do(tmp_path: Path) -> None:
    result = commands.main_menu(_fake("main_menu").seat(), tmp_path)

    assert result.exit_code == 10
    assert json.loads(result.line()) == {
        "action": "main-menu",
        "outcome": "nothing_to_do",
    }


def test_an_unknown_screen_saves_its_frame_and_says_so(tmp_path: Path) -> None:
    fake = _fake("unknown")
    frames = tmp_path / "frames"

    result = commands.main_menu(fake.seat(), frames)

    assert result.exit_code == 20
    line = json.loads(result.line())
    assert line["action"] == "main-menu"
    assert line["outcome"] == "unknown_screen"
    saved = list(frames.iterdir())
    assert [path.name for path in saved] == [line["frame"]]
    assert line["frame"].endswith(".png")
    with Image.open(saved[0]) as im:
        assert im.size == (1280, 720)
        assert screen.screen_name(im.convert("RGB")) == "unknown"


def test_a_saved_frame_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    frames = tmp_path / "frames"

    commands.main_menu(_fake("unknown").seat(), frames)

    (saved,) = frames.iterdir()
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(frames.stat().st_mode) == 0o700


def test_two_unknown_screens_in_the_same_second_keep_both_frames(
    tmp_path: Path,
) -> None:
    first = commands.main_menu(_fake("unknown").seat(), tmp_path)

    second = commands.main_menu(_fake("unknown").seat(), tmp_path)

    assert first.frame != second.frame
    assert {path.name for path in tmp_path.iterdir()} == {first.frame, second.frame}


def test_a_screen_that_never_arrives_saves_the_frame_it_waited_on(
    tmp_path: Path,
) -> None:
    states = without_exits(seat_states(), "multiplayer")

    result = commands.main_menu(FakeScreen(states, "multiplayer").seat(), tmp_path)

    assert result.exit_code == 20
    line = json.loads(result.line())
    assert line == {
        "action": "main-menu",
        "outcome": "unknown_screen",
        "code": "timeout_main_menu",
        "frame": line["frame"],
    }
    (saved,) = tmp_path.iterdir()
    assert saved.name == line["frame"]
    with Image.open(saved) as im:
        assert screen.mp_list(im.convert("RGB"))


def test_running_out_of_budget_is_a_failure(tmp_path: Path) -> None:
    fake = _fake("unknown")

    result = commands.main_menu(fake.seat(deadline=fake.now() + 30), tmp_path)

    assert result.exit_code == 1
    assert json.loads(result.line())["code"] == "budget"


def test_a_failing_screen_tool_is_a_failure_that_names_the_tool(
    tmp_path: Path,
) -> None:
    class BrokenFrames:
        def grab(self) -> Image.Image:
            raise subprocess.CalledProcessError(
                1, ["xwd", "-root"], stderr=b"xwd: unable to open display"
            )

    fake = _fake("multiplayer")
    seat = Seat(frames=BrokenFrames(), inputs=fake, clock=fake)

    result = commands.main_menu(seat, tmp_path)

    assert result.exit_code == 1
    assert json.loads(result.line()) == {
        "action": "main-menu",
        "outcome": "failed",
        "code": "command_failed_xwd_rc1_nodisplay",
    }


def test_the_result_is_one_line(tmp_path: Path) -> None:
    result = commands.main_menu(_fake("unknown").seat(), tmp_path)

    assert "\n" not in result.line()


# ------------------------------------------------------------ the CLI


def _seat_config(tmp_path: Path) -> Path:
    path = tmp_path / "seat.yaml"
    path.write_text("frames:\n  path: frames\n")
    return path


@pytest.mark.parametrize(
    ("start", "exit_code", "outcome"),
    [
        ("multiplayer", 0, "succeeded"),
        ("main_menu", 10, "nothing_to_do"),
        ("unknown", 20, "unknown_screen"),
    ],
)
def test_the_command_prints_one_json_line_and_exits_with_the_outcomes_code(
    start: str,
    exit_code: int,
    outcome: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _fake(start)

    @contextmanager
    def fake_live_seat(**_: object) -> Iterator[Seat]:
        yield fake.seat()

    monkeypatch.setattr(cli, "live_seat", fake_live_seat)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["seat", "main-menu", "--config", str(_seat_config(tmp_path))])

    assert exit_info.value.code == exit_code
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["outcome"] == outcome
    if outcome == "unknown_screen":
        assert len(list((tmp_path / "frames").iterdir())) == 1


def test_the_command_fails_with_a_json_line_when_the_seat_cannot_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def no_seat(**_: object) -> Iterator[Seat]:
        raise FileNotFoundError("xwd")
        yield

    monkeypatch.setattr(cli, "live_seat", no_seat)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["seat", "main-menu", "--config", str(_seat_config(tmp_path))])

    assert exit_info.value.code == 1
    assert json.loads(capsys.readouterr().out) == {
        "action": "main-menu",
        "outcome": "failed",
        "code": "tool_missing",
    }


@pytest.mark.parametrize("body", [None, "frames: [unclosed\n", "1: 2\n"])
def test_the_command_fails_with_a_json_line_when_its_config_is_unusable(
    body: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "seat.yaml"
    if body is not None:
        path.write_text(body)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["seat", "main-menu", "--config", str(path)])

    assert exit_info.value.code == 1
    assert json.loads(capsys.readouterr().out) == {
        "action": "main-menu",
        "outcome": "failed",
        "code": "config_error",
    }
