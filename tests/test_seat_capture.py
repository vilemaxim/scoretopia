"""`scoretopia seat capture-finished`, against the scripted fake screen."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fake_seat import (
    FakeScreen,
    FinishedGame,
    State,
    draw_replays,
    draw_unknown,
    replay_states,
    scroll_offsets,
)
from PIL import Image

from scoretopia import cli
from scoretopia.domain.captures import scan_manifests
from scoretopia.seat import capture, commands, screen
from scoretopia.seat.seam import Seat

SCREENS = {"setup": "setup.png", "info": "info.png", "victory": "victory.png"}


def _games(count: int) -> list[FinishedGame]:
    return [FinishedGame(pattern) for pattern in range(count)]


def _fake(
    games: list[FinishedGame],
    start: str = "main_menu",
    *,
    states: dict[str, State] | None = None,
    **faults: object,
) -> FakeScreen:
    return FakeScreen(states or replay_states(games), start, **faults)


def _run(fake: FakeScreen, root: Path, **settings: float) -> dict:
    result = commands.capture_finished(
        fake.seat(**settings), root / "captures", root / "frames"
    )
    line = json.loads(result.line())
    line["exit_code"] = result.exit_code
    return line


def _capture_dirs(root: Path) -> list[Path]:
    result = root / "captures" / "result"
    return sorted(result.iterdir()) if result.is_dir() else []


def _complete(root: Path) -> list[Path]:
    return [d for d in _capture_dirs(root) if (d / "manifest.json").is_file()]


def _opened(fake: FakeScreen) -> list[str]:
    """The games whose replay was opened, by index, in order."""
    return [
        name.split(":")[1].split("@")[0]
        for name in fake.visited
        if name.startswith("loading:")
    ]


def test_each_finished_game_gets_one_complete_capture(tmp_path: Path) -> None:
    fake = _fake(_games(3))

    line = _run(fake, tmp_path)

    assert line == {
        "action": "capture-finished",
        "outcome": "succeeded",
        "captured": 3,
        "skipped": 0,
        "list_end": True,
        "budget_reached": False,
        "exit_code": 0,
    }
    assert len(_capture_dirs(tmp_path)) == 3
    for directory in _capture_dirs(tmp_path):
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["kind"] == "result"
        assert manifest["start"] == "unknown"
        assert manifest["screens"] == SCREENS
        for file in SCREENS.values():
            with Image.open(directory / file) as im:
                assert im.size == (1280, 720)


def test_each_screen_saved_is_the_screen_it_is_named_for(tmp_path: Path) -> None:
    fake = _fake([FinishedGame(0), FinishedGame(1, ending="resigned")])

    _run(fake, tmp_path)

    endings = set()
    for directory in _capture_dirs(tmp_path):
        with Image.open(directory / "setup.png") as im:
            setup = im.convert("RGB")
        with Image.open(directory / "info.png") as im:
            info = im.convert("RGB")
        with Image.open(directory / "victory.png") as im:
            end = im.convert("RGB")
        top = screen.panel_top(setup)
        assert screen.find_blue(setup, 760, top + 250, top + 420) is not None
        assert screen.find_blue(setup, 640, top + 200, top + 300) is None
        assert screen.find_blue(info, 640, top + 200, top + 300) is not None
        assert screen.game_over(end)
        endings.add("victory" if screen.victory(end) else "resigned")
    assert endings == {"victory", "resigned"}


def test_the_time_in_the_manifest_is_when_the_first_screen_was_saved(
    tmp_path: Path,
) -> None:
    fake = _fake(_games(1))

    _run(fake, tmp_path)

    (directory,) = _capture_dirs(tmp_path)
    manifest = json.loads((directory / "manifest.json").read_text())
    # The fake's clock starts at 2027-01-15 08:00:00 UTC; a few seconds pass
    # before the panel is open, and the replay takes most of a minute.
    assert manifest["captured_at"].startswith("2027-01-15T08:00:")
    assert manifest["captured_at"].endswith("Z")
    assert fake.elapsed > 45


def test_the_manifest_is_written_last_and_the_record_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(_games(2))
    seen_at_write: list[tuple[str, list[str], str, bool]] = []
    write_json = capture.write_json

    def spying_write(path: Path, data: object, mode: int) -> None:
        manifests = _complete(tmp_path)
        seen_at_write.append(
            (
                path.name,
                sorted(p.name for p in path.parent.iterdir() if p.suffix == ".png"),
                fake.state.split("@")[0],
                len(manifests) > sum(1 for w in seen_at_write if w[0] == "record.json"),
            )
        )
        write_json(path, data, mode)

    monkeypatch.setattr(capture, "write_json", spying_write)

    _run(fake, tmp_path)

    screens = sorted(SCREENS.values())
    # Each manifest is written with all three screens saved and the client
    # back on the list; each record write finds that game's manifest there.
    assert seen_at_write == [
        ("manifest.json", screens, "replays", False),
        ("record.json", [], "replays", True),
        ("manifest.json", screens, "replays", False),
        ("record.json", [], "replays", True),
    ]


def test_the_run_ends_on_the_main_menu(tmp_path: Path) -> None:
    fake = _fake(_games(2), start="multiplayer")

    _run(fake, tmp_path)

    assert fake.state == "main_menu"


def test_a_second_run_captures_nothing_and_has_nothing_to_do(tmp_path: Path) -> None:
    games = _games(3)
    _run(_fake(games), tmp_path)
    second = _fake(games)

    line = _run(second, tmp_path)

    assert line["exit_code"] == 10
    assert line["outcome"] == "nothing_to_do"
    assert (line["captured"], line["skipped"]) == (0, 3)
    assert not any(name.startswith("panel:") for name in second.visited)
    assert len(_capture_dirs(tmp_path)) == 3


def test_a_game_finished_since_the_last_run_is_the_only_one_captured(
    tmp_path: Path,
) -> None:
    older = _games(2)
    _run(_fake(older), tmp_path)
    fake = _fake([FinishedGame(7), *older])

    line = _run(fake, tmp_path)

    assert (line["captured"], line["skipped"]) == (1, 2)
    assert _opened(fake) == ["0"]
    assert len(_complete(tmp_path)) == 3


def test_an_empty_replays_tab_is_nothing_to_do(tmp_path: Path) -> None:
    line = _run(_fake([]), tmp_path)

    assert line["exit_code"] == 10
    assert line["captured"] == 0
    assert _capture_dirs(tmp_path) == []


def test_a_list_opened_at_its_end_still_gives_every_game_once(tmp_path: Path) -> None:
    # Five rows do not fit: the top one starts under the tab bar, and the list
    # opens where it was last left, at its end.
    games = _games(5)
    end = scroll_offsets(5)[-1]
    fake = _fake(games, start=f"replays@{end}")

    line = _run(fake, tmp_path)

    assert line["captured"] == 5
    assert sorted(_opened(fake)) == ["0", "1", "2", "3", "4"]
    assert len(_complete(tmp_path)) == 5
    assert line["list_end"] is True


def test_the_ingest_scan_reads_what_the_command_wrote(tmp_path: Path) -> None:
    _run(_fake(_games(2)), tmp_path)

    manifests = scan_manifests(tmp_path / "captures")

    assert [m.kind for m in manifests] == ["result", "result"]
    for manifest in manifests:
        assert set(manifest.screens) == {"setup", "info", "victory"}
        assert all(path.is_file() for path in manifest.screens.values())
        assert manifest.start == "unknown"
        assert manifest.captured_at is not None


def test_captures_are_group_readable_and_the_record_is_private(tmp_path: Path) -> None:
    _run(_fake(_games(1)), tmp_path)

    (directory,) = _capture_dirs(tmp_path)
    record = tmp_path / "captures" / "record.json"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o2750
    assert stat.S_IMODE(directory.parent.stat().st_mode) == 0o2750
    for file in [*SCREENS.values(), "manifest.json"]:
        assert stat.S_IMODE((directory / file).stat().st_mode) == 0o640
    assert stat.S_IMODE(record.stat().st_mode) == 0o600


def test_nothing_forbidden_or_off_a_button_is_clicked(tmp_path: Path) -> None:
    fake = _fake(_games(5), start=f"replays@{scroll_offsets(5)[-1]}")

    _run(fake, tmp_path)

    assert fake.misses == []
    assert fake.hovering_grabs == 0


def test_stale_frames_and_dropped_clicks_do_not_stop_it(tmp_path: Path) -> None:
    fake = _fake(
        _games(2),
        stale_frames=1,
        stale_until_pointer_leaves=True,
        drop_unhovered_clicks=True,
    )

    line = _run(fake, tmp_path)

    assert line["exit_code"] == 0
    assert line["captured"] == 2
    assert fake.dropped_clicks == 0


def _with_broken_end(games: list[FinishedGame], index: int) -> dict[str, State]:
    """The states, where one game's Finish Game leads to an unknown screen."""
    states = replay_states(games)
    for name in list(states):
        if name.startswith(f"end:{index}@"):
            states[name] = State(name, draw_unknown())
    return states


def test_an_unknown_screen_leaves_no_capture_marked_complete(tmp_path: Path) -> None:
    games = _games(2)
    fake = _fake(games, states=_with_broken_end(games, 1))

    line = _run(fake, tmp_path)

    assert line["exit_code"] == 20
    assert line["outcome"] == "unknown_screen"
    assert line["code"] == "no_victory_screen"
    assert line["captured"] == 1
    assert [p.name for p in (tmp_path / "frames").iterdir()] == [line["frame"]]
    assert len(_complete(tmp_path)) == 1
    (half,) = set(_capture_dirs(tmp_path)) - set(_complete(tmp_path))
    assert not (half / "victory.png").exists()


def test_a_capture_stopped_half_way_is_taken_again_by_the_next_run(
    tmp_path: Path,
) -> None:
    games = _games(2)
    _run(_fake(games, states=_with_broken_end(games, 1)), tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["captured"], line["skipped"]) == (1, 1)
    assert _opened(fake) == ["1"]
    assert len(_complete(tmp_path)) == 2
    assert len(_capture_dirs(tmp_path)) == 2


def test_a_replay_that_never_finishes_is_left_by_exit_and_not_recorded(
    tmp_path: Path,
) -> None:
    fake = _fake([FinishedGame(0, plays_for=10_000)])

    line = _run(fake, tmp_path)

    assert line["exit_code"] == 20
    assert line["code"] == "finish_timeout"
    assert ("click", 765, 665, "exit") in fake.clicks
    assert _complete(tmp_path) == []
    assert fake.state == "main_menu"


def test_a_replay_that_does_not_open_is_not_waited_on_for_ten_minutes(
    tmp_path: Path,
) -> None:
    games = _games(1)
    states = replay_states(games)
    panel = states["panel:0@0"]
    states["panel:0@0"] = State(
        panel.name,
        panel.frame,
        buttons=panel.buttons,
        forbidden=panel.forbidden,
        on_click={k: v for k, v in panel.on_click.items() if k != "open"},
    )
    fake = _fake(games, states=states)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["code"]) == (20, "replay_not_open")
    assert fake.elapsed < 200
    assert _complete(tmp_path) == []


def test_a_record_with_anything_but_text_in_it_is_unreadable(tmp_path: Path) -> None:
    root = tmp_path / "captures"
    root.mkdir()
    (root / "record.json").write_text('{"result_masks": [1]}')

    line = _run(_fake(_games(1)), tmp_path)

    assert (line["exit_code"], line["code"]) == (1, "record_unreadable")


def test_no_capture_starts_without_the_time_to_finish_it(tmp_path: Path) -> None:
    fake = _fake(_games(2))

    line = _run(fake, tmp_path, deadline=fake.now() + 300)

    assert line["budget_reached"] is True
    assert line["captured"] == 0
    assert (line["exit_code"], line["code"]) == (1, "budget")
    assert not any(name.startswith("panel:") for name in fake.visited)
    assert fake.state == "main_menu"


def test_a_record_that_cannot_be_read_stops_the_run_before_any_capture(
    tmp_path: Path,
) -> None:
    root = tmp_path / "captures"
    root.mkdir()
    (root / "record.json").write_text("{not json")
    fake = _fake(_games(1))

    line = _run(fake, tmp_path)

    assert line["exit_code"] == 1
    assert line["code"] == "record_unreadable"
    assert fake.clicks == []


def test_the_record_holds_no_file_names_or_text_from_the_screen(
    tmp_path: Path,
) -> None:
    _run(_fake(_games(1)), tmp_path)

    record = json.loads((tmp_path / "captures" / "record.json").read_text())

    (directory,) = _capture_dirs(tmp_path)
    assert record["result"] == [directory.name]
    assert len(record["result_masks"]) == 1
    assert set(record) == {"setup", "result", "setup_masks", "result_masks"}


def test_a_record_written_before_this_command_existed_is_honoured(
    tmp_path: Path,
) -> None:
    # The routine this replaces kept the same file. This one is written by hand,
    # from the rows as the list draws them, not by the command.
    games = _games(3)
    listed = draw_replays(games, 0)
    rows = screen.bands(listed)
    record = {
        "setup": ["0123456789abcdef"],
        "setup_masks": [],
        "result": [screen.key_of(listed, row, "result") for row in rows[:2]],
        "result_masks": [screen.mask_of(listed, row, "result") for row in rows[:2]],
    }
    root = tmp_path / "captures"
    root.mkdir()
    (root / "record.json").write_text(json.dumps(record, indent=1, sort_keys=True))
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["captured"], line["skipped"]) == (1, 2)
    assert _opened(fake) == ["2"]
    kept = json.loads((root / "record.json").read_text())
    assert kept["setup"] == ["0123456789abcdef"]
    assert kept["result"][:2] == record["result"]


def test_more_games_than_fit_are_each_captured_once(tmp_path: Path) -> None:
    # Seven rows: on most pages one is cut by the list's bottom edge.
    games = _games(7)
    fake = _fake(games)

    line = _run(fake, tmp_path)
    again = _run(_fake(games, start=f"replays@{scroll_offsets(7)[2]}"), tmp_path)

    assert line["captured"] == 7
    assert sorted(_opened(fake)) == [str(n) for n in range(7)]
    assert len(_capture_dirs(tmp_path)) == 7
    assert (again["captured"], again["skipped"]) == (0, 7)


def _with_unknown_after(games: list[FinishedGame], prefix: str) -> dict[str, State]:
    """The states, where every state named ``<prefix>:...`` is an unknown screen."""
    states = replay_states(games)
    for name in list(states):
        if name.startswith(prefix + ":"):
            states[name] = State(name, draw_unknown())
    return states


@pytest.mark.parametrize(
    ("stage", "code"),
    [("panel", "timeout_replay_panel"), ("info", "timeout_info")],
)
def test_an_unrecognised_screen_at_any_stage_saves_it_and_completes_nothing(
    stage: str, code: str, tmp_path: Path
) -> None:
    games = _games(1)
    fake = _fake(games, states=_with_unknown_after(games, stage))

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["code"]) == (20, code)
    (saved,) = (tmp_path / "frames").iterdir()
    with Image.open(saved) as im:
        assert screen.screen_name(im.convert("RGB")) == "unknown"
    assert _complete(tmp_path) == []
    assert not (tmp_path / "captures" / "record.json").exists()


def test_the_command_prints_its_counts_on_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _fake(_games(1))

    @contextmanager
    def fake_live_seat(**_: object) -> Iterator[Seat]:
        yield fake.seat()

    monkeypatch.setattr(cli, "live_seat", fake_live_seat)
    config = tmp_path / "seat.yaml"
    config.write_text("captures:\n  path: captures\nframes:\n  path: frames\n")

    before = os.umask(0)
    try:
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["seat", "capture-finished", "--config", str(config)])
        during = os.umask(0)
    finally:
        os.umask(before)

    assert during == 0o027
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["captured"] == 1
    assert len(_complete(tmp_path)) == 1
