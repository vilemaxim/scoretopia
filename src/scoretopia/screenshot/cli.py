"""CLI for extracting data from Polytopia screenshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scoretopia.screenshot import extract as extract_mod


def _load_expected_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        print(f"Error: expected file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"Error: expected file is not valid JSON: {path}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    if not isinstance(expected, dict):
        print(f"Error: expected JSON must be an object: {path}", file=sys.stderr)
        raise SystemExit(1)
    return expected


def _write_or_print(text: str, output: Path | None) -> None:
    if output is not None:
        output.write_text(text, encoding="utf-8")
        print(f"Wrote extraction to {output}")
        return
    print(text, end="")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract structured data from a Polytopia screenshot."
    )
    parser.add_argument("image", type=Path, help="Path to the screenshot image")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write formatted extraction to this file (default: stdout)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format: human-readable text (default) or JSON",
    )
    parser.add_argument(
        "--expected",
        type=Path,
        help=(
            "Compare extraction JSON to this expected file "
            "and exit non-zero on mismatch"
        ),
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(".easyocr_models"),
        help="Directory for EasyOCR model files",
    )
    args = parser.parse_args()

    try:
        result = extract_mod.extract_screenshot(
            args.image, model_dir=args.model_dir
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"Error: OCR extraction failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.expected is not None:
        expected = _load_expected_json(args.expected)
        match, message = extract_mod.compare_extraction_to_expected(
            result, expected
        )
        print(message)
        if not match:
            raise SystemExit(1)
        return

    if args.format == "json":
        payload = extract_mod.serialize_extraction(result)
        _write_or_print(json.dumps(payload, indent=2) + "\n", args.output)
        return

    _write_or_print(extract_mod.format_extraction(result), args.output)


if __name__ == "__main__":
    main()
