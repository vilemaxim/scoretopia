"""Guard: production code must not embed human player names from sample goldens.

If this test fails, remove embedded names from ``src/scoretopia/`` and reimplement
with generic OCR/layout logic. Do not add sample names to an allowlist to make
the test pass.
"""

from __future__ import annotations

import ast
import io
import json
import tokenize
from pathlib import Path

import pytest

from scoretopia.domain.matching import is_bot_name

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "scoretopia"
SAMPLES_DIR = PROJECT_ROOT / "samples" / "screenshots"
MIN_FORBIDDEN_NAME_LEN = 4


def _load_forbidden_names(samples_dir: Path) -> frozenset[str]:
    """Collect human player names and winners from local sample JSON files."""
    if not samples_dir.is_dir():
        return frozenset()

    names: set[str] = set()
    for path in sorted(samples_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue

        winner = payload.get("winner")
        if isinstance(winner, str) and winner.strip():
            names.add(winner.strip())

        players = payload.get("players")
        if isinstance(players, list):
            for player in players:
                if not isinstance(player, dict):
                    continue
                name = player.get("name")
                if isinstance(name, str) and name.strip():
                    names.add(name.strip())

    return frozenset(
        name
        for name in names
        if not is_bot_name(name) and len(name) >= MIN_FORBIDDEN_NAME_LEN
    )


def _string_and_comment_texts(source: str) -> list[str]:
    """Return contents of string literals and comments in Python source."""
    texts: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts.append(node.value)

    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type == tokenize.COMMENT:
            texts.append(tok.string)
    return texts


def find_embedded_forbidden_names(
    source: str,
    forbidden_names: frozenset[str],
) -> list[tuple[str, str]]:
    """Return (forbidden_name, matched_text) for each hit in literals/comments."""
    if not forbidden_names:
        return []

    hits: list[tuple[str, str]] = []
    for fragment in _string_and_comment_texts(source):
        lowered = fragment.lower()
        for name in forbidden_names:
            if name.lower() in lowered:
                hits.append((name, fragment))
    return hits


def scan_src_for_embedded_sample_names(
    src_root: Path,
    forbidden_names: frozenset[str],
) -> list[str]:
    """Scan all Python files under src_root for forbidden sample names."""
    violations: list[str] = []
    if not src_root.is_dir():
        return violations

    for path in sorted(src_root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for forbidden_name, fragment in find_embedded_forbidden_names(
            source, forbidden_names
        ):
            rel = path.relative_to(PROJECT_ROOT)
            snippet = fragment.replace("\n", " ")[:80]
            violations.append(
                f"{rel}: forbidden name {forbidden_name!r} in literal/comment "
                f"({snippet!r})"
            )
    return violations


FORBIDDEN_NAMES = _load_forbidden_names(SAMPLES_DIR)


def test_no_embedded_sample_names_in_production_code() -> None:
    """Fail when any sample human name appears in src/scoretopia string literals."""
    if not FORBIDDEN_NAMES:
        pytest.skip("No local sample JSON names to guard against")

    violations = scan_src_for_embedded_sample_names(SRC_ROOT, FORBIDDEN_NAMES)
    assert not violations, "Embedded sample player names found:\n" + "\n".join(
        violations
    )


@pytest.mark.parametrize(
    ("forbidden_name", "source_snippet"),
    [
        ("xyzz", 'PLAYER = "prefix_xyzz_suffix"'),
        ("AbCdE", "# comment mentions AbCdE here"),
    ],
)
def test_embedded_name_scan_detects_literals_and_comments(
    forbidden_name: str,
    source_snippet: str,
) -> None:
    """Self-check: scan logic flags synthetic forbidden substrings."""
    hits = find_embedded_forbidden_names(
        source_snippet,
        frozenset({forbidden_name}),
    )
    assert hits, f"Expected scan to flag {forbidden_name!r} in {source_snippet!r}"


def test_forbidden_name_loader_skips_bots_and_short_names(tmp_path: Path) -> None:
    samples = tmp_path / "screenshots"
    samples.mkdir()
    (samples / "sample.json").write_text(
        json.dumps(
            {
                "winner": "Ab",
                "players": [
                    {"name": "Crazy Bot"},
                    {"name": "ValidName"},
                    {"name": "xy"},
                ],
            }
        ),
        encoding="utf-8",
    )

    names = _load_forbidden_names(samples)

    assert names == frozenset({"ValidName"})
