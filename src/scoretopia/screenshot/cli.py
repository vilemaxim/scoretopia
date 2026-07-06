"""CLI for extracting data from Polytopia screenshots."""

from __future__ import annotations

import argparse
from pathlib import Path

from scoretopia.screenshot.extract import write_extraction


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
        "--model-dir",
        type=Path,
        default=Path(".easyocr_models"),
        help="Directory for EasyOCR model files",
    )
    args = parser.parse_args()

    if args.output:
        write_extraction(args.image, args.output, model_dir=args.model_dir)
        print(f"Wrote extraction to {args.output}")
    else:
        from scoretopia.screenshot.extract import extract_screenshot, format_extraction

        result = extract_screenshot(args.image, model_dir=args.model_dir)
        print(format_extraction(result), end="")


if __name__ == "__main__":
    main()
