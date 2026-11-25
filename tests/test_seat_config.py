"""Seat control's configuration: its own file, with nothing of recording's."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scoretopia.config import ConfigError
from scoretopia.seat.config import SeatConfig, load_seat_config


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "seat.yaml"
    path.write_text(body)
    return path


def test_an_empty_file_gives_the_defaults(tmp_path: Path) -> None:
    config = load_seat_config(_write(tmp_path, ""))

    assert config.frames_path == (tmp_path / "data" / "seat-frames").resolve()
    assert config.budget_seconds == 3300
    assert config.display == ":99"


def test_settings_are_read_and_a_relative_path_resolves_beside_the_file(
    tmp_path: Path,
) -> None:
    body = 'frames:\n  path: seen\nbudget_seconds: 600\ndisplay: ":7"\nx_user: game\n'

    config = load_seat_config(_write(tmp_path, body))

    assert config == SeatConfig(
        frames_path=(tmp_path / "seen").resolve(),
        budget_seconds=600,
        display=":7",
        x_user="game",
    )


@pytest.mark.parametrize(
    "body",
    [
        "database:\n  path: data/scoretopia.db\n",
        "discord:\n  guild_id: '1'\n",
        "channels:\n  reports: somewhere\n",
        "token: abc\n",
        "frames:\n  path: seen\n  token: abc\n",
    ],
)
def test_a_recording_setting_is_refused(body: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown seat config key"):
        load_seat_config(_write(tmp_path, body))


def test_the_seat_configuration_has_no_field_for_a_token_or_a_database() -> None:
    names = {field.name for field in dataclasses.fields(SeatConfig)}

    assert names == {"frames_path", "budget_seconds", "display", "x_user"}


@pytest.mark.parametrize("budget", ["0", "-5", "3600", "true", "'60'"])
def test_a_budget_must_be_a_whole_number_of_seconds_under_an_hour(
    budget: str, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="budget_seconds"):
        load_seat_config(_write(tmp_path, f"budget_seconds: {budget}\n"))


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_seat_config(tmp_path / "absent.yaml")


@pytest.mark.parametrize("body", ["frames: false\n", "frames: 0\n", "frames: ''\n"])
def test_frames_must_be_a_mapping(body: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="frames must be a mapping"):
        load_seat_config(_write(tmp_path, body))


def test_a_file_that_is_not_yaml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unreadable"):
        load_seat_config(_write(tmp_path, "frames: [unclosed\n"))
