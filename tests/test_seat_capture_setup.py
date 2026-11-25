"""`scoretopia seat capture-setup`, against the scripted fake screen."""

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
    OngoingGame,
    State,
    draw_invitation,
    draw_ongoing,
    draw_unknown,
    draw_wide_button_panel,
    ongoing_states,
    scroll_offsets,
)
from PIL import Image, ImageDraw

from scoretopia import cli
from scoretopia.domain.captures import scan_manifests
from scoretopia.seat import capture, commands, screen
from scoretopia.seat.seam import Seat

SCREENS = {"card": "card.png", "info": "info.png"}


def _games(count: int) -> list[OngoingGame]:
    return [OngoingGame(pattern) for pattern in range(count)]


def _fake(
    games: list[OngoingGame],
    start: str = "main_menu",
    *,
    states: dict[str, State] | None = None,
    **faults: object,
) -> FakeScreen:
    return FakeScreen(states or ongoing_states(games), start, **faults)


def _run(fake: FakeScreen, root: Path, **settings: float) -> dict:
    result = commands.capture_setup(
        fake.seat(**settings), root / "captures", root / "frames"
    )
    line = json.loads(result.line())
    line["exit_code"] = result.exit_code
    return line


def _capture_dirs(root: Path) -> list[Path]:
    setup = root / "captures" / "setup"
    return sorted(setup.iterdir()) if setup.is_dir() else []


def _complete(root: Path) -> list[Path]:
    return [d for d in _capture_dirs(root) if (d / "manifest.json").is_file()]


def _opened(fake: FakeScreen) -> list[str]:
    """The cards whose panel was opened, by index, in order."""
    # Game Info closes back to the panel, which is not another opening.
    return [
        name.split(":")[1].split("@")[0]
        for before, name in zip(fake.visited, fake.visited[1:])
        if name.startswith("card:") and before.startswith("ongoing@")
    ]


def test_each_uncaptured_game_gets_one_complete_setup_capture(tmp_path: Path) -> None:
    fake = _fake(_games(3))

    line = _run(fake, tmp_path)

    assert line == {
        "action": "capture-setup",
        "outcome": "succeeded",
        "captured": 3,
        "skipped": 0,
        "not_started": 0,
        "list_end": True,
        "budget_reached": False,
        "exit_code": 0,
    }
    assert len(_capture_dirs(tmp_path)) == 3
    for directory in _capture_dirs(tmp_path):
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["kind"] == "setup"
        assert manifest["start"] == "unknown"
        assert manifest["screens"] == SCREENS
        for file in SCREENS.values():
            with Image.open(directory / file) as im:
                assert im.size == (1280, 720)
    assert fake.state == "main_menu"


def test_each_screen_saved_is_the_screen_it_is_named_for(tmp_path: Path) -> None:
    _run(_fake(_games(2)), tmp_path)

    for directory in _capture_dirs(tmp_path):
        with Image.open(directory / "card.png") as im:
            card = im.convert("RGB")
        with Image.open(directory / "info.png") as im:
            info = im.convert("RGB")
        top = screen.panel_top(card)
        assert screen.find_blue(card, 760, top + 250, top + 420) is not None
        assert screen.find_blue(card, 640, top + 200, top + 300) is None
        assert screen.find_blue(info, 640, top + 200, top + 300) is not None


def test_a_second_run_captures_nothing_and_has_nothing_to_do(tmp_path: Path) -> None:
    games = _games(3)
    _run(_fake(games), tmp_path)
    second = _fake(games)

    line = _run(second, tmp_path)

    assert line["exit_code"] == 10
    assert line["outcome"] == "nothing_to_do"
    assert (line["captured"], line["skipped"]) == (0, 3)
    assert _opened(second) == []
    assert len(_capture_dirs(tmp_path)) == 3


def test_a_game_started_since_the_last_run_is_the_only_one_captured(
    tmp_path: Path,
) -> None:
    older = _games(2)
    _run(_fake(older), tmp_path)
    fake = _fake([OngoingGame(7), *older])

    line = _run(fake, tmp_path)

    assert (line["captured"], line["skipped"]) == (1, 2)
    assert _opened(fake) == ["0"]
    assert len(_complete(tmp_path)) == 3


def test_the_record_says_whether_a_games_setup_is_captured(tmp_path: Path) -> None:
    captured = _games(2)
    _run(_fake(captured), tmp_path)
    # The list as a later run finds it: a new game above the two captured.
    listed = draw_ongoing([OngoingGame(7), *captured], 0)

    record = capture.CaptureRecord(tmp_path / "captures" / "record.json")
    answers = [
        record.knows("setup", screen.mask_variants(listed, row, "setup"))
        for row in screen.bands(listed)
    ]

    assert answers == [False, True, True]


def test_the_record_keeps_setups_apart_from_finished_games(tmp_path: Path) -> None:
    _run(_fake(_games(1)), tmp_path)

    kept = json.loads((tmp_path / "captures" / "record.json").read_text())

    (directory,) = _capture_dirs(tmp_path)
    assert kept["setup"] == [directory.name]
    assert len(kept["setup_masks"]) == 1
    assert (kept["result"], kept["result_masks"]) == ([], [])


def test_the_ingest_scan_reads_what_the_command_wrote(tmp_path: Path) -> None:
    _run(_fake(_games(2)), tmp_path)

    manifests = scan_manifests(tmp_path / "captures")

    assert [m.kind for m in manifests] == ["setup", "setup"]
    for manifest in manifests:
        assert set(manifest.screens) == {"card", "info"}
        assert all(path.is_file() for path in manifest.screens.values())
        assert manifest.start == "unknown"
        assert manifest.captured_at is not None


def test_the_manifest_is_written_last_and_the_record_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(_games(1))
    seen_at_write: list[tuple[str, list[str], str]] = []
    write_json = capture.write_json

    def spying_write(path: Path, data: object, mode: int) -> None:
        seen_at_write.append(
            (
                path.name,
                sorted(p.name for p in path.parent.iterdir() if p.suffix == ".png"),
                fake.state.split("@")[0],
            )
        )
        write_json(path, data, mode)

    monkeypatch.setattr(capture, "write_json", spying_write)

    _run(fake, tmp_path)

    # The manifest is written with both screens saved and the panel closed.
    assert seen_at_write == [
        ("manifest.json", ["card.png", "info.png"], "ongoing"),
        ("record.json", [], "ongoing"),
    ]


def test_captures_are_group_readable_and_the_record_is_private(tmp_path: Path) -> None:
    _run(_fake(_games(1)), tmp_path)

    (directory,) = _capture_dirs(tmp_path)
    record = tmp_path / "captures" / "record.json"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o2750
    assert stat.S_IMODE(directory.parent.stat().st_mode) == 0o2750
    for file in [*SCREENS.values(), "manifest.json"]:
        assert stat.S_IMODE((directory / file).stat().st_mode) == 0o640
    assert stat.S_IMODE(record.stat().st_mode) == 0o600


@pytest.mark.parametrize("kind", ["lobby", "invitation"])
def test_a_game_that_has_not_started_is_closed_and_not_recorded(
    kind: str, tmp_path: Path
) -> None:
    games = [OngoingGame(0, kind), OngoingGame(1)]
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["captured"], line["not_started"]) == (0, 1, 1)
    assert _opened(fake) == ["0", "1"]
    assert len(_capture_dirs(tmp_path)) == 1
    assert fake.misses == []
    # Once it starts, its card is captured: nothing recorded it.
    started = _fake([OngoingGame(0), OngoingGame(1)])
    later = _run(started, tmp_path)
    assert (later["captured"], later["skipped"]) == (1, 1)
    assert _opened(started) == ["0"]


def test_an_invitation_whose_accept_cannot_be_found_is_still_closed(
    tmp_path: Path,
) -> None:
    # Capturing needs only the invitation's BACK.
    def unlabelled() -> Image.Image:
        frame = draw_invitation()
        ImageDraw.Draw(frame).rectangle((700, 490, 800, 507), fill=(255, 255, 255))
        return frame

    games = [OngoingGame(0, "invitation"), OngoingGame(1)]
    states = ongoing_states(games)
    card = states["card:0@0"]
    states["card:0@0"] = State(
        card.name,
        unlabelled,
        buttons=card.buttons,
        forbidden=card.forbidden,
        on_click=card.on_click,
    )
    fake = _fake(games, start="ongoing@0", states=states)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["captured"], line["not_started"]) == (0, 1, 1)


def test_an_invitation_is_neither_accepted_nor_declined(tmp_path: Path) -> None:
    fake = _fake([OngoingGame(0, "invitation")])

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["outcome"]) == (10, "nothing_to_do")
    assert "accept" not in [click[3] for click in fake.clicks]
    assert _capture_dirs(tmp_path) == []


def test_a_lobby_the_seat_could_start_stops_the_run_with_nothing_pressed(
    tmp_path: Path,
) -> None:
    fake = _fake([OngoingGame(0, "own_lobby")], start="ongoing@0")

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["code"]) == (20, "card_panel_without_open")
    assert [click[3] for click in fake.clicks] == ["card0"]
    assert _capture_dirs(tmp_path) == []
    assert [p.name for p in (tmp_path / "frames").iterdir()] == [line["frame"]]


@pytest.mark.parametrize(
    "button",
    [(560, 500, 740, 533), (560, 500, 690, 533), (620, 500, 740, 533)],
    ids=["both_sides", "left", "right"],
)
def test_a_centre_button_wider_than_a_lobbys_back_is_not_pressed(
    button: tuple[int, int, int, int], tmp_path: Path
) -> None:
    # Whatever such a button does, it is not the BACK of a joined lobby.
    states = ongoing_states(_games(1))
    states["card:0@0"] = State(
        "card:0@0",
        draw_wide_button_panel(button),
        forbidden={"start_game": button},
    )
    fake = _fake(_games(1), start="ongoing@0", states=states)

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["code"]) == (20, "lobby_without_back")
    assert [click[3] for click in fake.clicks] == ["card0"]
    assert _capture_dirs(tmp_path) == []


def _with_unknown_after(games: list[OngoingGame], prefix: str) -> dict[str, State]:
    """The states, where every state named ``<prefix>:...`` is an unknown screen."""
    states = ongoing_states(games)
    for name in list(states):
        if name.startswith(prefix + ":"):
            states[name] = State(name, draw_unknown())
    return states


@pytest.mark.parametrize(
    ("stage", "code"),
    [("card", "timeout_card_panel"), ("cardinfo", "timeout_info")],
)
def test_an_unknown_screen_saves_its_frame_and_completes_nothing(
    stage: str, code: str, tmp_path: Path
) -> None:
    games = _games(1)
    fake = _fake(games, states=_with_unknown_after(games, stage))

    line = _run(fake, tmp_path)

    assert (line["exit_code"], line["outcome"]) == (20, "unknown_screen")
    assert line["code"] == code
    (saved,) = (tmp_path / "frames").iterdir()
    assert saved.name == line["frame"]
    with Image.open(saved) as im:
        assert screen.screen_name(im.convert("RGB")) == "unknown"
    assert _complete(tmp_path) == []
    assert not (tmp_path / "captures" / "record.json").exists()


def test_a_capture_stopped_half_way_is_taken_again_by_the_next_run(
    tmp_path: Path,
) -> None:
    games = _games(1)
    _run(_fake(games, states=_with_unknown_after(games, "cardinfo")), tmp_path)
    fake = _fake(games)

    line = _run(fake, tmp_path)

    assert (line["captured"], line["skipped"]) == (1, 0)
    assert len(_complete(tmp_path)) == 1
    assert len(_capture_dirs(tmp_path)) == 1


def test_more_games_than_fit_are_each_captured_once(tmp_path: Path) -> None:
    # Seven cards: the list opens at its end, with its top cards out of sight.
    games = _games(7)
    fake = _fake(games)

    line = _run(fake, tmp_path)
    again = _run(_fake(games, start=f"ongoing@{scroll_offsets(7)[2]}"), tmp_path)

    assert line["captured"] == 7
    assert sorted(_opened(fake)) == [str(n) for n in range(7)]
    assert len(_capture_dirs(tmp_path)) == 7
    assert (again["captured"], again["skipped"]) == (0, 7)
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

    assert (line["exit_code"], line["captured"]) == (0, 2)
    assert fake.dropped_clicks == 0


def test_no_capture_starts_without_the_time_to_finish_it(tmp_path: Path) -> None:
    fake = _fake(_games(2))

    line = _run(fake, tmp_path, deadline=fake.now() + 100)

    assert line["budget_reached"] is True
    assert (line["exit_code"], line["code"], line["captured"]) == (1, "budget", 0)
    assert _opened(fake) == []
    assert fake.state == "main_menu"


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
            cli.main(["seat", "capture-setup", "--config", str(config)])
        during = os.umask(0)
    finally:
        os.umask(before)

    assert during == 0o027
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["captured"] == 1
    assert len(_complete(tmp_path)) == 1
