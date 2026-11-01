"""Read steam-test capture screens with the existing Steam OCR pipeline."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from scoretopia.domain.captures import (
    CaptureExtractionError,
    CaptureManifest,
    ResultCapture,
    SetupCapture,
)
from scoretopia.screenshot.models import (
    ExtractionResult,
    GameBasicsExtraction,
    GameEndExtraction,
)

ExtractFn = Callable[[Path], ExtractionResult]
ReadTextFn = Callable[[Path], list[str]]

# A Polytopia game id as a GUID. Assumed from the More info panel's id, not
# yet checked against a capture: an id this does not find leaves de-duplication
# to the capture directory.
_GAME_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def parse_game_id(lines: Iterable[str]) -> str | None:
    """The first GUID in the OCR lines of a More info panel, lower-cased."""
    for line in lines:
        match = _GAME_ID.search(line.replace(" ", ""))
        if match:
            return match.group(0).lower()
    return None


class OcrCaptureExtractor:
    """:class:`~scoretopia.domain.captures.CaptureExtractor` over EasyOCR."""

    def __init__(
        self,
        *,
        extract: ExtractFn | None = None,
        read_text: ReadTextFn | None = None,
    ) -> None:
        self._extract = extract or _default_extract
        self._read_text = read_text or _default_read_text

    def extract_setup(self, manifest: CaptureManifest) -> SetupCapture:
        return SetupCapture(
            basics=self._basics(manifest.screens["card"]),
            game_id=self._game_id(manifest),
        )

    def extract_result(self, manifest: CaptureManifest) -> ResultCapture:
        victory = self._extract(manifest.screens["victory"])
        if not isinstance(victory, GameEndExtraction):
            raise CaptureExtractionError("victory screen not recognised")
        return ResultCapture(
            basics=self._basics(manifest.screens["setup"]),
            victory=victory,
            game_id=self._game_id(manifest),
        )

    def _basics(self, path: Path) -> GameBasicsExtraction:
        result = self._extract(path)
        if not isinstance(result, GameBasicsExtraction):
            raise CaptureExtractionError("setup panel not recognised")
        return result

    def _game_id(self, manifest: CaptureManifest) -> str | None:
        info = manifest.screens.get("info")
        if info is None or not info.is_file():
            return None
        try:
            return parse_game_id(self._read_text(info))
        except Exception:
            # The id only de-duplicates; a capture without one still counts.
            return None


def _default_extract(path: Path) -> ExtractionResult:
    from scoretopia.screenshot.extract import extract_screenshot

    return extract_screenshot(path)


def _default_read_text(path: Path) -> list[str]:
    from scoretopia.screenshot.extract import DEFAULT_MODEL_DIR, _ocr_reader

    reader = _ocr_reader(str(DEFAULT_MODEL_DIR))
    return [str(text) for text in reader.readtext(str(path), detail=0)]
