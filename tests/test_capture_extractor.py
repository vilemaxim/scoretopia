"""OcrCaptureExtractor with stubbed OCR: screen routing and game id parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from scoretopia.domain.captures import CaptureExtractionError, CaptureManifest
from scoretopia.screenshot.captures import OcrCaptureExtractor, parse_game_id
from scoretopia.screenshot.models import (
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
)

GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
BASICS = GameBasicsExtraction(players=(GameBasicsPlayer(name="Alpha"),))
VICTORY = GameEndExtraction(winner="Alpha")


def _manifest(tmp_path: Path, kind: str, screens: dict[str, str]) -> CaptureManifest:
    for file in screens.values():
        (tmp_path / file).write_bytes(b"")
    return CaptureManifest(
        directory=tmp_path,
        relative_path=f"{kind}/k",
        kind=kind,
        captured_at=None,
        start="unknown",
        screens={name: tmp_path / file for name, file in screens.items()},
    )


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["Game ID", GUID], GUID),
        ([f"id: {GUID.upper()}"], GUID),
        (["0f8fad5b-d9cb-469f- a165-70867728950e"], GUID),
        (["Game ID", "not an id"], None),
        ([], None),
    ],
)
def test_parse_game_id(lines: list[str], expected: str | None) -> None:
    assert parse_game_id(lines) == expected


def test_setup_reads_the_card_and_the_info_id(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png", "info": "info.png"})
    seen: list[str] = []

    def extract(path: Path):
        seen.append(path.name)
        return BASICS

    extractor = OcrCaptureExtractor(extract=extract, read_text=lambda _: [GUID])

    capture = extractor.extract_setup(manifest)

    assert seen == ["card.png"]
    assert capture.basics == BASICS
    assert capture.game_id == GUID


def test_result_reads_setup_and_victory(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        "result",
        {"setup": "setup.png", "info": "info.png", "victory": "victory.png"},
    )
    answers = {"setup.png": BASICS, "victory.png": VICTORY}
    extractor = OcrCaptureExtractor(
        extract=lambda path: answers[path.name],
        read_text=lambda _: [],
    )

    capture = extractor.extract_result(manifest)

    assert capture.basics == BASICS
    assert capture.victory == VICTORY
    assert capture.game_id is None


def test_wrong_screen_type_is_an_extraction_error(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png"})
    extractor = OcrCaptureExtractor(
        extract=lambda _: FriendProfileExtraction(),
        read_text=lambda _: [],
    )

    with pytest.raises(CaptureExtractionError):
        extractor.extract_setup(manifest)


def test_unreadable_info_leaves_the_id_unset(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png", "info": "info.png"})

    def broken(_: Path) -> list[str]:
        raise RuntimeError("ocr failed")

    extractor = OcrCaptureExtractor(extract=lambda _: BASICS, read_text=broken)

    assert extractor.extract_setup(manifest).game_id is None
