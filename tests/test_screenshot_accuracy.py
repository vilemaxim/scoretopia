"""Tests for the per-field OCR accuracy harness (no sample images needed)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scoretopia.screenshot.accuracy import (
    FieldTally,
    discover_golden_pairs,
    field_results,
    format_accuracy,
    main,
    resolve_extraction_names,
    roster_from_goldens,
    run_accuracy,
)
from scoretopia.screenshot.models import (
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndHeader,
    GameEndPlayer,
)


def _game_end_expected() -> dict:
    return {
        "screenshot_type": "game_end",
        "winner": "Alpha",
        "header": {"score": 500, "stars": 7, "stars_gained": 2, "turn": 0},
        "players": [
            {
                "name": "Alpha",
                "tribe": "Bardur",
                "status": None,
                "score": 500,
                "elo_change": 5,
                "elo": 1200,
                "is_winner": True,
            },
            {
                "name": "Bravo",
                "tribe": "Xin-xi",
                "status": "resigned on turn 0",
                "score": None,
                "elo_change": -5,
                "elo": 900,
                "is_winner": False,
            },
        ],
    }


def _game_basics_expected() -> dict:
    return {
        "screenshot_type": "game_basics",
        "game_name": "Test Map",
        "map_size": 121,
        "terrain": "Pangea",
        "target_score": 5000,
        "game_type": "Glory",
        "game_timer": None,
        "win_condition_text": "First to reach 5,000 points win",
        "turn_status": None,
        "players": [
            {"name": "Alpha", "is_you": False, "is_eliminated": False},
            {"name": "Bravo", "is_you": False, "is_eliminated": False},
        ],
    }


def test_field_results_all_correct_for_identical_game_end() -> None:
    expected = _game_end_expected()
    results = field_results(expected, expected)
    assert results
    assert all(ok for _field, ok in results)
    fields = {field for field, _ok in results}
    assert "game_end.winner" in fields
    assert "game_end.header.turn" in fields
    assert "game_end.players.tribe" in fields
    assert "game_end.players.count" in fields


def test_field_results_counts_each_player_once_per_field() -> None:
    expected = _game_end_expected()
    results = field_results(expected, expected)
    tribe_rows = [ok for field, ok in results if field == "game_end.players.tribe"]
    assert tribe_rows == [True, True]


def test_field_results_flags_wrong_scalar_and_player_field() -> None:
    expected = _game_end_expected()
    actual = json.loads(json.dumps(expected))
    actual["header"]["turn"] = 3
    actual["players"][1]["tribe"] = "Imperius"
    results = dict(
        (field, ok)
        for field, ok in field_results(actual, expected)
        if field in {"game_end.header.turn", "game_end.header.score"}
    )
    assert results == {"game_end.header.turn": False, "game_end.header.score": True}
    tribe_rows = [
        ok
        for field, ok in field_results(actual, expected)
        if field == "game_end.players.tribe"
    ]
    assert tribe_rows == [True, False]


def test_field_results_names_use_fuzzy_match_for_game_end() -> None:
    expected = _game_end_expected()
    actual = json.loads(json.dumps(expected))
    actual["winner"] = "Alph"
    actual["players"][0]["name"] = "Alph"
    results = dict(field_results(actual, expected))
    assert results["game_end.winner"] is True
    names = [
        ok
        for field, ok in field_results(actual, expected)
        if field.endswith("players.name")
    ]
    assert names == [True, True]


def test_field_results_names_exact_for_game_basics() -> None:
    expected = _game_basics_expected()
    actual = json.loads(json.dumps(expected))
    actual["players"][0]["name"] = "Alph"
    names = [
        ok
        for field, ok in field_results(actual, expected)
        if field == "game_basics.players.name"
    ]
    assert names == [False, True]


def test_field_results_missing_player_counts_as_wrong() -> None:
    expected = _game_basics_expected()
    actual = json.loads(json.dumps(expected))
    actual["players"] = actual["players"][:1]
    results = field_results(actual, expected)
    assert dict(results)["game_basics.players.count"] is False
    names = [ok for field, ok in results if field == "game_basics.players.name"]
    assert names == [True, False]


def test_field_results_type_mismatch_marks_every_field_wrong() -> None:
    expected = _game_end_expected()
    actual = _game_basics_expected()
    results = field_results(actual, expected)
    assert results
    assert not any(ok for _field, ok in results)


def test_field_results_none_actual_marks_every_field_wrong() -> None:
    expected = _game_basics_expected()
    results = field_results(None, expected)
    assert dict(results)["screenshot_type"] is False
    assert not any(ok for _field, ok in results)


def test_field_tally_counts_correct_and_total() -> None:
    tally = FieldTally()
    tally.add([("a", True), ("a", False), ("b", True)])
    tally.add([("a", True)])
    assert tally.counts == {"a": (2, 3), "b": (1, 1)}


def test_format_accuracy_prints_counts_only() -> None:
    tally = FieldTally()
    tally.add([("game_end.winner", True), ("game_end.header.turn", False)])
    text = format_accuracy(tally, samples=1, failed=0)
    assert "game_end.winner" in text
    assert "1/1" in text
    assert "0/1" in text
    assert "samples: 1" in text
    assert "total: 1/2" in text


def test_discover_golden_pairs_only_includes_matching_json(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"img")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.png").write_bytes(b"img")
    pairs = discover_golden_pairs(tmp_path)
    assert [(i.name, g.name) for i, g in pairs] == [("a.png", "a.json")]


def test_discover_golden_pairs_missing_dir(tmp_path: Path) -> None:
    assert discover_golden_pairs(tmp_path / "nope") == []


def _write_pair(directory: Path, stem: str, expected: dict) -> None:
    (directory / f"{stem}.png").write_bytes(b"img")
    (directory / f"{stem}.json").write_text(json.dumps(expected), encoding="utf-8")


def test_run_accuracy_tallies_extractions(tmp_path: Path) -> None:
    _write_pair(tmp_path, "end", _game_end_expected())
    _write_pair(tmp_path, "basics", _game_basics_expected())

    end = GameEndExtraction(
        winner="Alpha",
        header=GameEndHeader(score=500, stars=7, stars_gained=2, turn=0),
        players=(
            GameEndPlayer(
                name="Alpha",
                tribe="Bardur",
                score=500,
                elo_change=5,
                elo=1200,
                is_winner=True,
            ),
            GameEndPlayer(
                name="Bravo",
                tribe="Xin-xi",
                status="resigned on turn 0",
                elo_change=-5,
                elo=900,
            ),
        ),
    )

    def fake_extract(path: Path, *, model_dir: Path) -> object:
        if Path(path).stem == "end":
            return end
        raise ValueError("Unrecognized screenshot type")

    with patch(
        "scoretopia.screenshot.accuracy.extract_screenshot", side_effect=fake_extract
    ):
        tally, samples, failed = run_accuracy(tmp_path, model_dir=tmp_path)

    assert samples == 2
    assert failed == 1
    assert tally.counts["game_end.winner"] == (1, 1)
    assert tally.counts["game_basics.map_size"] == (0, 1)


def test_main_prints_counts(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _write_pair(tmp_path, "basics", _game_basics_expected())
    result = GameBasicsExtraction(
        game_name="Test Map",
        map_size=121,
        terrain="Pangea",
        target_score=5000,
        game_type="Glory",
        win_condition_text="First to reach 5,000 points win",
        players=(GameBasicsPlayer(name="Alpha"), GameBasicsPlayer(name="Bravo")),
    )
    with patch(
        "scoretopia.screenshot.accuracy.extract_screenshot", return_value=result
    ):
        main([str(tmp_path), "--model-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "game_basics.map_size" in out
    assert "Alpha" not in out
    assert "total:" in out


def test_roster_from_goldens_collects_players_and_winner(tmp_path: Path) -> None:
    _write_pair(tmp_path, "end", _game_end_expected())
    _write_pair(tmp_path, "basics", _game_basics_expected())
    roster = roster_from_goldens(discover_golden_pairs(tmp_path))
    assert roster == ["Alpha", "Bravo"]


def test_resolve_extraction_names_uses_roster_spelling() -> None:
    result = GameBasicsExtraction(
        players=(GameBasicsPlayer(name="al.pha"), GameBasicsPlayer(name="zulu")),
    )
    resolved = resolve_extraction_names(result, ["alpha", "Bravo"])
    assert [p.name for p in resolved.players] == ["alpha", "zulu"]


def test_resolve_extraction_names_game_end_winner() -> None:
    result = GameEndExtraction(
        winner="bravol",
        players=(GameEndPlayer(name="bravol", is_winner=True),),
    )
    resolved = resolve_extraction_names(result, ["bravo1"])
    assert resolved.winner == "bravo1"
    assert resolved.players[0].name == "bravo1"


def test_main_no_roster_flag(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _write_pair(tmp_path, "basics", _game_basics_expected())
    result = GameBasicsExtraction(players=(GameBasicsPlayer(name="Alpha"),))
    with patch(
        "scoretopia.screenshot.accuracy.extract_screenshot", return_value=result
    ):
        main([str(tmp_path), "--no-roster", "--model-dir", str(tmp_path)])
    assert "roster: off" in capsys.readouterr().out
