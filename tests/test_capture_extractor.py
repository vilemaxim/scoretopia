"""OcrCaptureExtractor with stubbed extraction: screen routing and the game id."""

from __future__ import annotations

from pathlib import Path

import pytest

from scoretopia.domain.captures import CaptureExtractionError, CaptureManifest
from scoretopia.screenshot.captures import OcrCaptureExtractor
from scoretopia.screenshot.models import (
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameInfoExtraction,
)

GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
BASICS = GameBasicsExtraction(players=(GameBasicsPlayer(name="Alpha"),))
VICTORY = GameEndExtraction(winner="Alpha")
INFO = GameInfoExtraction(game_id=GUID, game_version=1, turn=0)


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


def test_setup_reads_the_card_and_the_info_id(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png", "info": "info.png"})
    seen: list[str] = []

    def extract(path: Path):
        seen.append(path.name)
        return INFO if path.name == "info.png" else BASICS

    extractor = OcrCaptureExtractor(extract=extract)

    capture = extractor.extract_setup(manifest)

    assert seen == ["card.png", "info.png"]
    assert capture.basics == BASICS
    assert capture.game_id == GUID


def test_result_reads_setup_and_victory(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        "result",
        {"setup": "setup.png", "info": "info.png", "victory": "victory.png"},
    )
    answers = {"setup.png": BASICS, "victory.png": VICTORY, "info.png": INFO}
    extractor = OcrCaptureExtractor(extract=lambda path: answers[path.name])

    capture = extractor.extract_result(manifest)

    assert capture.basics == BASICS
    assert capture.victory == VICTORY
    assert capture.game_id == GUID


def test_missing_info_screen_leaves_the_id_unset(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png"})
    extractor = OcrCaptureExtractor(extract=lambda _: BASICS)

    assert extractor.extract_setup(manifest).game_id is None


def test_info_without_an_id_leaves_the_id_unset(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png", "info": "info.png"})
    answers = {"card.png": BASICS, "info.png": GameInfoExtraction()}
    extractor = OcrCaptureExtractor(extract=lambda path: answers[path.name])

    assert extractor.extract_setup(manifest).game_id is None


def test_info_read_as_another_screen_leaves_the_id_unset(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png", "info": "info.png"})
    extractor = OcrCaptureExtractor(extract=lambda _: BASICS)

    assert extractor.extract_setup(manifest).game_id is None


def test_wrong_screen_type_is_an_extraction_error(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png"})
    extractor = OcrCaptureExtractor(extract=lambda _: FriendProfileExtraction())

    with pytest.raises(CaptureExtractionError):
        extractor.extract_setup(manifest)


def test_unreadable_info_leaves_the_id_unset(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "setup", {"card": "card.png", "info": "info.png"})

    def extract(path: Path):
        if path.name == "info.png":
            raise RuntimeError("ocr failed")
        return BASICS

    extractor = OcrCaptureExtractor(extract=extract)

    assert extractor.extract_setup(manifest).game_id is None
