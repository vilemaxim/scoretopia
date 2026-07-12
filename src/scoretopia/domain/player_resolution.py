"""DB-assisted OCR player roster resolution.

Fuzzy matching uses a fixed SequenceMatcher ratio threshold of 0.80
(``scoretopia.screenshot.name_matching._FUZZY_MATCH_THRESHOLD``). The
threshold is not configurable in v1 — see ADR 004.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from typing import Literal

from scoretopia.domain.matching import is_bot_name
from scoretopia.screenshot.models import (
    ExtractionResult,
    FriendProfileExtraction,
    GameBasicsExtraction,
    GameBasicsPlayer,
    GameEndExtraction,
    GameEndPlayer,
)
from scoretopia.screenshot.name_matching import (
    _FUZZY_MATCH_THRESHOLD,
    normalize_ocr_name,
)
from scoretopia.storage.repos import PlayerRepo

MatchType = Literal["exact", "fuzzy", "new"]


@dataclass(frozen=True)
class RosterSlotResolution:
    raw_ocr: str
    suggested_name: str | None
    confidence: float
    match_type: MatchType


def resolve_roster_slots(
    roster_names: Sequence[str],
    player_repo: PlayerRepo,
    *,
    screenshot_type: str,
) -> list[RosterSlotResolution]:
    """Resolve human OCR roster names against the players table.

    ``screenshot_type`` is accepted for callers (game_basics / game_end);
    both types use the same fixed 0.80 fuzzy threshold in v1.
    """
    _ = screenshot_type  # reserved; threshold is shared across types in v1
    known: list[tuple[str, str]] = [
        (player.polytopia_name, normalize_ocr_name(player.polytopia_name).lower())
        for player in player_repo.list_all()
    ]
    return [
        _resolve_one(raw_name, known)
        for raw_name in roster_names
        if not is_bot_name(raw_name)
    ]


def _resolve_one(
    raw_name: str,
    known: Sequence[tuple[str, str]],
) -> RosterSlotResolution:
    normalized = normalize_ocr_name(raw_name).lower()
    for canonical, known_normalized in known:
        if normalized == known_normalized:
            return RosterSlotResolution(
                raw_ocr=raw_name,
                suggested_name=canonical,
                confidence=1.0,
                match_type="exact",
            )

    best_name: str | None = None
    best_ratio = 0.0
    for canonical, known_normalized in known:
        ratio = difflib.SequenceMatcher(None, normalized, known_normalized).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = canonical

    if best_name is not None and best_ratio >= _FUZZY_MATCH_THRESHOLD:
        return RosterSlotResolution(
            raw_ocr=raw_name,
            suggested_name=best_name,
            confidence=best_ratio,
            match_type="fuzzy",
        )

    return RosterSlotResolution(
        raw_ocr=raw_name,
        suggested_name=None,
        confidence=best_ratio,
        match_type="new",
    )


def roster_names_from_extraction(extraction: ExtractionResult) -> list[str]:
    if isinstance(extraction, (GameBasicsExtraction, GameEndExtraction)):
        return [player.name for player in extraction.players]
    return []


def apply_exact_resolutions(
    extraction: ExtractionResult,
    resolved: Sequence[RosterSlotResolution],
) -> ExtractionResult:
    """Return a working extraction with exact-match suggestions applied.

    Fuzzy and new slots keep their raw OCR names until the uploader confirms.
    """
    if isinstance(extraction, FriendProfileExtraction):
        return extraction

    suggestions = {
        index: slot.suggested_name
        for index, slot in enumerate(resolved)
        if slot.match_type == "exact" and slot.suggested_name
    }
    if not suggestions:
        return extraction

    if isinstance(extraction, GameBasicsExtraction):
        return replace(
            extraction,
            players=tuple(
                _apply_exact_to_basics_players(extraction.players, suggestions)
            ),
        )

    if isinstance(extraction, GameEndExtraction):
        winner = extraction.winner
        if winner is not None:
            for slot in resolved:
                if (
                    slot.match_type == "exact"
                    and slot.raw_ocr == winner
                    and slot.suggested_name
                ):
                    winner = slot.suggested_name
                    break
        return replace(
            extraction,
            winner=winner,
            players=tuple(
                _apply_exact_to_end_players(extraction.players, suggestions)
            ),
        )

    return extraction


def _apply_exact_to_basics_players(
    players: Sequence[GameBasicsPlayer],
    suggestions: dict[int, str],
) -> list[GameBasicsPlayer]:
    result: list[GameBasicsPlayer] = []
    human_index = 0
    for player in players:
        if is_bot_name(player.name):
            result.append(player)
            continue
        suggested = suggestions.get(human_index)
        human_index += 1
        if suggested is not None and suggested != player.name:
            result.append(replace(player, name=suggested))
        else:
            result.append(player)
    return result


def _apply_exact_to_end_players(
    players: Sequence[GameEndPlayer],
    suggestions: dict[int, str],
) -> list[GameEndPlayer]:
    result: list[GameEndPlayer] = []
    human_index = 0
    for player in players:
        if is_bot_name(player.name):
            result.append(player)
            continue
        suggested = suggestions.get(human_index)
        human_index += 1
        if suggested is not None and suggested != player.name:
            result.append(replace(player, name=suggested))
        else:
            result.append(player)
    return result


def initial_slot_confirmations(
    resolved: Sequence[RosterSlotResolution],
) -> dict[int, bool]:
    """Exact slots are auto-confirmed; fuzzy/new start unconfirmed.

    Continue gating (ADR 005) uses ``fix_resolved_roster_slots``, not
    acknowledgement-only ``slot_confirmations``.
    """
    return {
        index: slot.match_type == "exact" for index, slot in enumerate(resolved)
    }


def resolved_roster_as_dicts(
    resolved: Sequence[RosterSlotResolution],
) -> list[dict[str, object]]:
    return [asdict(slot) for slot in resolved]


def human_roster_index_for_player_slot(
    players: Sequence[dict[str, object]],
    player_slot_index: int,
) -> int | None:
    """Map extraction ``players`` index → human-only roster index (bots skipped)."""
    human_index = 0
    for index, entry in enumerate(players):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        if is_bot_name(name):
            continue
        if index == player_slot_index:
            return human_index
        human_index += 1
    return None


def mark_roster_slot_fix_resolved(
    payload: dict[str, object],
    *,
    player_slot_index: int,
) -> None:
    """Record that a fuzzy/new slot was resolved via the Fix path."""
    extraction = payload.get("extraction")
    if not isinstance(extraction, dict):
        return
    players = extraction.get("players")
    if not isinstance(players, list):
        return
    human_index = human_roster_index_for_player_slot(players, player_slot_index)
    if human_index is None:
        return
    fix_resolved = payload.get("fix_resolved_roster_slots")
    if not isinstance(fix_resolved, dict):
        fix_resolved = {}
    fix_resolved[str(human_index)] = True
    payload["fix_resolved_roster_slots"] = fix_resolved
    confirmations = payload.get("slot_confirmations")
    if isinstance(confirmations, dict):
        confirmations[str(human_index)] = True


def unresolved_fuzzy_new_slot_indexes(
    payload: dict[str, object],
) -> tuple[int, ...]:
    """Human roster indexes still needing Fix before continue_review."""
    resolved_roster = payload.get("resolved_roster")
    if not isinstance(resolved_roster, list):
        return ()
    fix_resolved = payload.get("fix_resolved_roster_slots")
    if not isinstance(fix_resolved, dict):
        fix_resolved = {}
    unresolved: list[int] = []
    for index, entry in enumerate(resolved_roster):
        if not isinstance(entry, dict):
            continue
        match_type = entry.get("match_type")
        if match_type not in {"fuzzy", "new"}:
            continue
        if fix_resolved.get(str(index)) is True:
            continue
        unresolved.append(index)
    return tuple(unresolved)


def player_slot_indexes_by_human_roster(
    players: Sequence[dict[str, object]],
) -> dict[int, int]:
    """Map human roster index → extraction ``players`` list index."""
    mapping: dict[int, int] = {}
    human_index = 0
    for player_index, entry in enumerate(players):
        if not isinstance(entry, dict):
            continue
        if is_bot_name(str(entry.get("name", ""))):
            continue
        mapping[human_index] = player_index
        human_index += 1
    return mapping


def mark_all_unresolved_roster_slots_fix_resolved(
    payload: dict[str, object],
) -> None:
    """Mark every fuzzy/new slot Fix-resolved (accept current extraction names)."""
    unresolved = unresolved_fuzzy_new_slot_indexes(payload)
    if not unresolved:
        return
    extraction = payload.get("extraction")
    if not isinstance(extraction, dict):
        return
    players = extraction.get("players")
    if not isinstance(players, list):
        return
    human_to_player = player_slot_indexes_by_human_roster(players)
    for human_idx in unresolved:
        player_idx = human_to_player.get(human_idx)
        if player_idx is None:
            continue
        mark_roster_slot_fix_resolved(
            payload,
            player_slot_index=player_idx,
        )
