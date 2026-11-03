"""Read steam-test capture screens with the existing Steam OCR pipeline."""

from __future__ import annotations

from collections.abc import Callable
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
    GameInfoExtraction,
)

ExtractFn = Callable[[Path], ExtractionResult]


class OcrCaptureExtractor:
    """:class:`~scoretopia.domain.captures.CaptureExtractor` over EasyOCR."""

    def __init__(self, *, extract: ExtractFn | None = None) -> None:
        self._extract = extract or _default_extract

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
        """The id from the ``game_info`` extraction of ``info.png``.

        The id only de-duplicates: an unread one leaves that to the capture
        directory, and the capture still counts.
        """
        info = manifest.screens.get("info")
        if info is None or not info.is_file():
            return None
        try:
            result = self._extract(info)
        except Exception:
            return None
        if not isinstance(result, GameInfoExtraction):
            return None
        return result.game_id


def _default_extract(path: Path) -> ExtractionResult:
    from scoretopia.screenshot.extract import extract_screenshot

    return extract_screenshot(path)
