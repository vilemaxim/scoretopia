"""Discovery-based OCR golden tests for samples/screenshots/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples" / "screenshots"
MODEL_DIR = PROJECT_ROOT / ".easyocr_models"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def discover_golden_pairs(samples_dir: Path) -> list[tuple[Path, Path]]:
    """Return (image, json) pairs where both files exist under samples_dir."""
    if not samples_dir.is_dir():
        return []
    pairs: list[tuple[Path, Path]] = []
    for path in sorted(samples_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        golden = path.with_suffix(".json")
        if golden.is_file():
            pairs.append((path, golden))
    return pairs


GOLDEN_PAIRS = discover_golden_pairs(SAMPLES_DIR)
FIXIOOOIAN_SAMPLE = SAMPLES_DIR / "fixioooian_butte-start.png"
FIXIOOOIAN_GOLDEN = FIXIOOOIAN_SAMPLE.with_suffix(".json")


def test_discover_golden_pairs_only_includes_matching_json(tmp_path: Path) -> None:
    samples = tmp_path / "screenshots"
    samples.mkdir()
    (samples / "paired.png").write_bytes(b"img")
    (samples / "paired.json").write_text("{}", encoding="utf-8")
    (samples / "unpaired.jpg").write_bytes(b"img")
    (samples / "notes.txt").write_text("ignore", encoding="utf-8")
    (samples / "also.jpeg").write_bytes(b"img")
    (samples / "also.json").write_text("{}", encoding="utf-8")

    pairs = discover_golden_pairs(samples)
    names = {(image.name, golden.name) for image, golden in pairs}

    assert names == {
        ("paired.png", "paired.json"),
        ("also.jpeg", "also.json"),
    }


def test_discover_golden_pairs_empty_when_no_json(tmp_path: Path) -> None:
    samples = tmp_path / "screenshots"
    samples.mkdir()
    (samples / "lonely.png").write_bytes(b"img")

    assert discover_golden_pairs(samples) == []


@pytest.mark.parametrize(
    ("image_path", "golden_path"),
    GOLDEN_PAIRS,
    ids=[p[0].name for p in GOLDEN_PAIRS],
)
def test_sample_screenshot_matches_golden_player_names(
    image_path: Path,
    golden_path: Path,
) -> None:
    from scoretopia.screenshot.extract import (
        compare_extraction_player_names,
        extract_screenshot,
    )

    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    result = extract_screenshot(image_path, model_dir=MODEL_DIR)
    match, message = compare_extraction_player_names(result, expected)
    assert match, f"{image_path.name}: {message}"


def test_sample_golden_parametrization_skips_when_no_local_pairs() -> None:
    """No image+json pairs => zero parametrized cases (not failures)."""
    if GOLDEN_PAIRS:
        pytest.skip("Local golden pairs present")
    assert GOLDEN_PAIRS == []


def test_fixioooian_in_golden_pairs_when_both_files_present() -> None:
    """Task 026: replay-menu sample participates in discovery-based goldens."""
    if not FIXIOOOIAN_SAMPLE.is_file():
        pytest.skip("Local fixioooian_butte-start sample not present")
    assert FIXIOOOIAN_GOLDEN.is_file(), (
        "Missing fixioooian_butte-start.json for local golden pair"
    )

    pair_names = {image.name for image, _golden in GOLDEN_PAIRS}
    assert "fixioooian_butte-start.png" in pair_names
