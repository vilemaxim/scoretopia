"""Tests for the local-only samples/screenshots/ directory (Task 001)."""

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = PROJECT_ROOT / ".gitignore"
AGENT_CONTEXT = PROJECT_ROOT / ".agent-context.md"
SAMPLES_SCREENSHOTS_DIR = PROJECT_ROOT / "samples" / "screenshots"


def _samples_paragraph() -> str:
    """The paragraph of .agent-context.md that describes samples/screenshots/."""
    paragraphs = AGENT_CONTEXT.read_text().split("\n\n")
    matching = [text for text in paragraphs if "samples/screenshots" in text]
    assert matching, "samples/screenshots/ is not described in .agent-context.md"
    return " ".join(matching)


def test_gitignore_ignores_samples_screenshots_directory() -> None:
    content = GITIGNORE.read_text()
    assert "samples/screenshots/" in content


def test_git_check_ignore_reports_samples_screenshots() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-v", "samples/screenshots/example.png"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "Expected samples/screenshots/example.png to be ignored by git. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "samples/screenshots" in result.stdout


def test_agent_context_documents_samples_screenshots() -> None:
    described = _samples_paragraph().lower()

    assert "screenshot" in described
    assert any(
        term in described
        for term in ("local", "not committed", "gitignore", "local-only")
    )


@pytest.mark.skipif(
    not SAMPLES_SCREENSHOTS_DIR.exists(),
    reason="samples/screenshots/ is gitignored and absent in this checkout",
)
def test_samples_screenshots_directory_exists() -> None:
    assert SAMPLES_SCREENSHOTS_DIR.is_dir()
