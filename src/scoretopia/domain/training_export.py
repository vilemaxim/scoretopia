"""Write training artifact bundles after successful ingest commit."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ARTIFACT_SCREENSHOT = "screenshot.png"
_ARTIFACT_OCR_RAW = "ocr_raw.json"
_ARTIFACT_OCR_RESOLVED = "ocr_resolved.json"
_ARTIFACT_COMMITTED = "committed.json"
_ARTIFACT_METADATA = "metadata.json"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_dump(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_extraction(
    payload: dict[str, object],
    key: str,
) -> dict[str, Any]:
    value = payload.get(key)
    if isinstance(value, dict):
        return value
    # Fallback when key is absent (e.g. pre-028 payloads without raw_extraction).
    return _as_dict(payload.get("extraction"))


def _correction_count(payload: dict[str, object]) -> int:
    corrections = payload.get("corrections")
    if not isinstance(corrections, list):
        return 0
    return sum(1 for entry in corrections if isinstance(entry, dict))


def _mod_approvals(payload: dict[str, object]) -> list[dict[str, object]]:
    approvals = payload.get("mod_approvals")
    if not isinstance(approvals, list):
        return []
    result: list[dict[str, object]] = []
    for entry in approvals:
        if not isinstance(entry, dict):
            continue
        mod_id = entry.get("mod_discord_id")
        approved_at = entry.get("approved_at")
        if isinstance(mod_id, str) and isinstance(approved_at, str):
            result.append(
                {"mod_discord_id": mod_id, "approved_at": approved_at},
            )
    return result


def export_training_bundle(
    *,
    training_path: Path,
    interaction_id: int,
    screenshot_path: Path,
    payload: dict[str, object],
    committed_extraction: dict[str, object],
) -> None:
    """Write the five-file training artifact bundle for one committed ingest.

    Layout: ``{training_path}/{YYYY-MM-DD}/{interaction_id}/``.
    """
    now = _utc_now()
    bundle_dir = training_path / now.strftime("%Y-%m-%d") / str(interaction_id)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(screenshot_path, bundle_dir / _ARTIFACT_SCREENSHOT)

    uploader = payload.get("uploader_discord_id")
    screenshot_type = payload.get("screenshot_type")
    metadata: dict[str, object] = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uploader_discord_id": uploader if isinstance(uploader, str) else "",
        "screenshot_type": (
            screenshot_type if isinstance(screenshot_type, str) else ""
        ),
        "correction_count": _correction_count(payload),
        "mod_approvals": _mod_approvals(payload),
        "interaction_id": interaction_id,
    }

    _json_dump(
        bundle_dir / _ARTIFACT_OCR_RAW,
        _payload_extraction(payload, "raw_extraction"),
    )
    _json_dump(
        bundle_dir / _ARTIFACT_OCR_RESOLVED,
        _payload_extraction(payload, "resolved_extraction"),
    )
    _json_dump(bundle_dir / _ARTIFACT_COMMITTED, committed_extraction)
    _json_dump(bundle_dir / _ARTIFACT_METADATA, metadata)

    logger.info(
        "training bundle written path=%s interaction_id=%s",
        bundle_dir,
        interaction_id,
    )


def try_export_training_bundle(
    *,
    training_path: Path | None,
    interaction_id: int,
    screenshot_path: Path,
    payload: dict[str, object],
    committed_extraction: dict[str, object],
) -> None:
    """Export training artifacts; log WARNING on failure without raising."""
    if training_path is None:
        return
    try:
        export_training_bundle(
            training_path=training_path,
            interaction_id=interaction_id,
            screenshot_path=screenshot_path,
            payload=payload,
            committed_extraction=committed_extraction,
        )
    except Exception as exc:  # noqa: BLE001 — must not fail ingest commit
        logger.warning(
            "training export failed interaction_id=%s path=%s error=%s",
            interaction_id,
            training_path,
            exc,
        )
