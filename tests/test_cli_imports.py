"""The CLI must not pay for EasyOCR and torch just by being imported."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def test_importing_the_cli_imports_neither_easyocr_nor_torch() -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, scoretopia.cli; "
            "sys.exit('easyocr' in sys.modules or 'torch' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
