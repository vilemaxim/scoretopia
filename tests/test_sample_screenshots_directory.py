"""Tests for the local-only samples/screenshots/ directory (Task 001)."""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = PROJECT_ROOT / ".gitignore"
AGENT_CONTEXT = PROJECT_ROOT / ".agent-context.md"
SAMPLES_SCREENSHOTS_DIR = PROJECT_ROOT / "samples" / "screenshots"
KEY_AREAS_MARKER = "## 📂 Key Areas"


def _key_areas_content() -> str:
    content = AGENT_CONTEXT.read_text()
    assert KEY_AREAS_MARKER in content, (
        "Key Areas section missing from .agent-context.md"
    )
    return content[content.index(KEY_AREAS_MARKER) :]


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
    key_areas = _key_areas_content()

    assert "samples/screenshots" in key_areas
    assert "screenshot" in key_areas.lower()
    assert "flat" in key_areas.lower()
    assert any(
        term in key_areas.lower()
        for term in ("local", "not committed", "gitignore", "local-only")
    )
    assert any(
        term in key_areas.lower() for term in ("upload", "human", "owner")
    )


def test_samples_screenshots_directory_exists() -> None:
    assert SAMPLES_SCREENSHOTS_DIR.is_dir()
