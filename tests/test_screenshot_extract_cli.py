"""Tests for scoretopia-extract JSON format and --expected verification."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scoretopia.screenshot.models import (
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndHeader,
    GameEndPlayer,
    WinRatio,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _sample_game_end() -> GameEndExtraction:
    return GameEndExtraction(
        winner="Alice",
        header=GameEndHeader(score=1000, stars=50, stars_gained=10, turn=20),
        players=(
            GameEndPlayer(
                name="Alice",
                tribe="Imperius",
                score=1000,
                elo_change=25,
                elo=1200,
                is_winner=True,
            ),
            GameEndPlayer(name="Bob", tribe="Bardur", score=800, is_winner=False),
        ),
    )


def _sample_game_basics() -> GameBasicsExtraction:
    return GameBasicsExtraction(
        game_name="Friday Night",
        map_size=30,
        terrain="Drylands",
        target_score=25_000,
        game_type="Persia",
        game_timer="24h",
        win_condition_text="Last standing",
        turn_status="Your turn",
        players=(
            GameBasicsPlayer(name="Alice", is_you=True),
            GameBasicsPlayer(name="Bob", is_eliminated=True),
        ),
    )


def _sample_friend_profile() -> FriendProfileExtraction:
    return FriendProfileExtraction(
        friend_name="Bob",
        alias="Bobby",
        num_friends=5,
        games_played=40,
        game_version=122,
        elo=1100,
        win_ratio=WinRatio(
            you_name="Alice",
            you_wins=3,
            friend_name="Bob",
            friend_wins=7,
        ),
    )


def test_serialize_extraction_game_end_shape() -> None:
    from scoretopia.screenshot.extract import serialize_extraction

    payload = serialize_extraction(_sample_game_end())

    assert payload["screenshot_type"] == "game_end"
    assert payload["winner"] == "Alice"
    assert payload["header"]["score"] == 1000
    assert payload["header"]["turn"] == 20
    assert isinstance(payload["players"], list)
    assert payload["players"][0]["name"] == "Alice"
    assert payload["players"][0]["is_winner"] is True
    assert payload["players"][1]["name"] == "Bob"


def test_serialize_extraction_game_basics_shape() -> None:
    from scoretopia.screenshot.extract import serialize_extraction

    payload = serialize_extraction(_sample_game_basics())

    assert payload["screenshot_type"] == "game_basics"
    assert payload["game_name"] == "Friday Night"
    assert payload["map_size"] == 30
    assert payload["players"][0] == {
        "name": "Alice",
        "is_you": True,
        "is_eliminated": False,
    }


def test_serialize_extraction_friend_profile_shape() -> None:
    from scoretopia.screenshot.extract import serialize_extraction

    payload = serialize_extraction(_sample_friend_profile())

    assert payload["screenshot_type"] == "friend_profile"
    assert payload["friend_name"] == "Bob"
    assert payload["win_ratio"]["you_wins"] == 3
    assert payload["win_ratio"]["friend_wins"] == 7


def test_serialize_extraction_round_trips_json() -> None:
    from scoretopia.screenshot.extract import serialize_extraction

    for result in (
        _sample_game_end(),
        _sample_game_basics(),
        _sample_friend_profile(),
    ):
        payload = serialize_extraction(result)
        encoded = json.dumps(payload)
        assert json.loads(encoded) == payload


def test_compare_extraction_to_expected_matches() -> None:
    from scoretopia.screenshot.extract import (
        compare_extraction_to_expected,
        serialize_extraction,
    )

    result = _sample_game_basics()
    expected = serialize_extraction(result)

    match, message = compare_extraction_to_expected(result, expected)

    assert match is True
    assert message


def test_compare_extraction_to_expected_reports_mismatch() -> None:
    from scoretopia.screenshot.extract import (
        compare_extraction_to_expected,
        serialize_extraction,
    )

    result = _sample_game_basics()
    expected = serialize_extraction(result)
    expected = {**expected, "game_name": "Wrong Name"}

    match, message = compare_extraction_to_expected(result, expected)

    assert match is False
    assert (
        "Wrong Name" in message
        or "game_name" in message
        or "Friday Night" in message
    )


def test_compare_extraction_to_expected_ignores_key_order() -> None:
    from scoretopia.screenshot.extract import compare_extraction_to_expected

    result = _sample_friend_profile()
    expected = {
        "elo": 1100,
        "alias": "Bobby",
        "friend_name": "Bob",
        "screenshot_type": "friend_profile",
        "num_friends": 5,
        "games_played": 40,
        "game_version": 122,
        "win_ratio": {
            "friend_wins": 7,
            "you_wins": 3,
            "friend_name": "Bob",
            "you_name": "Alice",
        },
    }

    match, _message = compare_extraction_to_expected(result, expected)

    assert match is True


def test_cli_format_json_prints_serialized_extraction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scoretopia.screenshot.cli import main
    from scoretopia.screenshot.extract import serialize_extraction

    image = tmp_path / "shot.png"
    image.write_bytes(b"fake-image")
    result = _sample_game_end()

    monkeypatch.setattr(
        "sys.argv",
        ["scoretopia-extract", str(image), "--format", "json"],
    )
    with patch(
        "scoretopia.screenshot.extract.extract_screenshot",
        return_value=result,
    ):
        main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == serialize_extraction(result)


def test_cli_expected_exits_zero_on_match(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scoretopia.screenshot.cli import main
    from scoretopia.screenshot.extract import serialize_extraction

    image = tmp_path / "shot.png"
    image.write_bytes(b"fake-image")
    result = _sample_game_basics()
    expected_path = tmp_path / "shot.json"
    expected_path.write_text(
        json.dumps(serialize_extraction(result), indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        ["scoretopia-extract", str(image), "--expected", str(expected_path)],
    )
    with patch(
        "scoretopia.screenshot.extract.extract_screenshot",
        return_value=result,
    ):
        main()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "pass" in combined.lower() or "match" in combined.lower()


def test_cli_expected_exits_nonzero_on_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scoretopia.screenshot.cli import main
    from scoretopia.screenshot.extract import serialize_extraction

    image = tmp_path / "shot.png"
    image.write_bytes(b"fake-image")
    result = _sample_game_basics()
    expected = serialize_extraction(result)
    expected["game_name"] = "Not The Real Name"
    expected_path = tmp_path / "shot.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        ["scoretopia-extract", str(image), "--expected", str(expected_path)],
    )
    with (
        patch(
            "scoretopia.screenshot.extract.extract_screenshot",
            return_value=result,
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code not in (0, None)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert combined.strip()
    assert (
        "Not The Real Name" in combined
        or "game_name" in combined
        or "mismatch" in combined.lower()
        or "differ" in combined.lower()
        or "Friday Night" in combined
    )


def test_cli_accepts_format_and_expected_flags(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scoretopia.screenshot.cli import main

    monkeypatch.setattr(
        "sys.argv",
        ["scoretopia-extract", "--help"],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    help_text = captured.out + captured.err
    assert "--format" in help_text
    assert "--expected" in help_text
    assert "json" in help_text.lower()


def test_cli_expected_exits_nonzero_when_expected_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scoretopia.screenshot.cli import main

    image = tmp_path / "shot.png"
    image.write_bytes(b"fake-image")
    missing = tmp_path / "missing.json"

    monkeypatch.setattr(
        "sys.argv",
        ["scoretopia-extract", str(image), "--expected", str(missing)],
    )
    with (
        patch(
            "scoretopia.screenshot.extract.extract_screenshot",
            return_value=_sample_game_end(),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code not in (0, None)
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    # Must be a handled missing-expected error, not argparse rejecting the flag.
    assert "unrecognized arguments" not in combined
    assert (
        str(missing).lower() in combined
        or "expected" in combined
        or "not found" in combined
        or "missing" in combined
    )


def test_compare_extraction_player_names_matches_on_names_only() -> None:
    from scoretopia.screenshot.extract import (
        compare_extraction_player_names,
        serialize_extraction,
    )

    result = _sample_game_basics()
    expected = serialize_extraction(result)
    expected = {**expected, "game_name": "Wrong Title", "map_size": 999}

    match, message = compare_extraction_player_names(result, expected)

    assert match is True
    assert "player" in message.lower() or "name" in message.lower()


def test_compare_extraction_player_names_reports_name_mismatch() -> None:
    from scoretopia.screenshot.extract import (
        compare_extraction_player_names,
        serialize_extraction,
    )

    result = _sample_game_basics()
    expected = serialize_extraction(result)
    expected["players"][0]["name"] = "NotAlice"

    match, message = compare_extraction_player_names(result, expected)

    assert match is False
    assert "NotAlice" in message or "Alice" in message
    assert "index" in message.lower() or "0" in message


def test_compare_extraction_player_names_ignores_score_fields() -> None:
    from scoretopia.screenshot.extract import (
        compare_extraction_player_names,
        serialize_extraction,
    )

    result = _sample_game_end()
    expected = serialize_extraction(result)
    expected["players"][0]["score"] = 0
    expected["header"]["score"] = 0

    match, _message = compare_extraction_player_names(result, expected)

    assert match is True


def test_cli_expected_names_only_exits_zero_when_names_match(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scoretopia.screenshot.cli import main
    from scoretopia.screenshot.extract import serialize_extraction

    image = tmp_path / "shot.png"
    image.write_bytes(b"fake-image")
    result = _sample_game_basics()
    expected = serialize_extraction(result)
    expected = {**expected, "game_name": "Different Name"}
    expected_path = tmp_path / "shot.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "scoretopia-extract",
            str(image),
            "--expected",
            str(expected_path),
            "--expected-names-only",
        ],
    )
    with patch(
        "scoretopia.screenshot.extract.extract_screenshot",
        return_value=result,
    ):
        main()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "name" in combined.lower() or "match" in combined.lower()


def test_cli_expected_names_only_exits_nonzero_on_name_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scoretopia.screenshot.cli import main
    from scoretopia.screenshot.extract import serialize_extraction

    image = tmp_path / "shot.png"
    image.write_bytes(b"fake-image")
    result = _sample_game_basics()
    expected = serialize_extraction(result)
    expected["players"][0]["name"] = "WrongPlayer"
    expected_path = tmp_path / "shot.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "scoretopia-extract",
            str(image),
            "--expected",
            str(expected_path),
            "--expected-names-only",
        ],
    )
    with (
        patch(
            "scoretopia.screenshot.extract.extract_screenshot",
            return_value=result,
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code not in (0, None)


def test_cli_accepts_expected_names_only_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scoretopia.screenshot.cli import main

    monkeypatch.setattr("sys.argv", ["scoretopia-extract", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    help_text = (capsys.readouterr().out + capsys.readouterr().err).lower()
    assert "--expected-names-only" in help_text


def test_readme_documents_extract_json_and_expected() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "scoretopia-extract" in readme
    assert "--format" in readme or "format json" in readme
    assert "--expected" in readme


def test_readme_documents_player_name_goldens_and_no_embed_policy() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()

    assert "player" in readme and "name" in readme
    assert "no-embed" in readme or "must not" in readme or "hardcode" in readme
