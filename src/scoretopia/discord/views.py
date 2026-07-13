"""Discord UI views and interaction helpers.

Create a bot at https://discord.com/developers, invite it with the
``attachments`` and ``applications.commands`` scopes, and set channel names in
``config/scoretopia.yaml``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import discord

from scoretopia.domain.matching import is_bot_name
from scoretopia.storage.models import Game, Player

_CUSTOM_ID_PREFIX = "st"
_MAX_GAME_PICK_OPTIONS = 25
_MAX_PLAYER_PICK_OPTIONS = 25


@dataclass(frozen=True)
class ParsedCustomId:
    action: str
    interaction_id: int
    game_id: int | None = None
    player_slot: int | None = None
    field: str | None = None


def unauthorized_confirmation_message() -> str:
    return "not your confirmation"


def can_confirm_game_end(*, uploader_discord_id: str, actor_discord_id: str) -> bool:
    return uploader_discord_id == actor_discord_id


def can_confirm_win_ratio(
    *, other_player_discord_id: str, actor_discord_id: str
) -> bool:
    return other_player_discord_id == actor_discord_id


def can_confirm_extraction(
    *, uploader_discord_id: str, actor_discord_id: str
) -> bool:
    """Uploader-only gate for diagnosis Continue/Fix/Abandon (legacy name)."""
    return uploader_discord_id == actor_discord_id


def can_review_staged(
    *, uploader_discord_id: str, actor_discord_id: str
) -> bool:
    """Preferred alias for diagnosis-review authorization (ADR 005)."""
    return can_confirm_extraction(
        uploader_discord_id=uploader_discord_id,
        actor_discord_id=actor_discord_id,
    )


def can_confirm_final_summary(
    *, uploader_discord_id: str, actor_discord_id: str
) -> bool:
    return uploader_discord_id == actor_discord_id


def can_confirm_player_link(
    *, selected_discord_user_id: str, actor_discord_id: str
) -> bool:
    return selected_discord_user_id == actor_discord_id


def can_approve_mod_batch(
    *,
    bot_mod_discord_ids: Sequence[str],
    actor_discord_id: str,
) -> bool:
    return actor_discord_id in bot_mod_discord_ids


_PLAYER_LINK_ACTIONS = frozenset(
    {
        "confirm_player_spelling",
        "reject_player_spelling",
        "pick_player_correction",
        "confirm_player_link",
        "reject_player_link",
        "select_player_discord_user",
        "accept_roster_suggestion",
        "pick_roster_known_player",
        "override_roster_name",
        "select_roster_known_player",
        "submit_roster_override",
        "remove_roster_player",
        "move_roster_player_up",
        "move_roster_player_down",
    }
)

_FIX_FIELD_ACTIONS = frozenset(
    {
        "pick_field_correction",
        "submit_field_correction",
        "add_roster_player",
        "submit_add_roster_player",
    }
)


def encode_custom_id(
    action: str,
    *,
    interaction_id: int,
    game_id: int | None = None,
    player_slot: int | None = None,
    field: str | None = None,
) -> str:
    if field is not None:
        return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}:{field}"
    if game_id is not None:
        return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}:{game_id}"
    if player_slot is not None:
        return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}:{player_slot}"
    return f"{_CUSTOM_ID_PREFIX}:{action}:{interaction_id}"


def parse_custom_id(custom_id: str) -> ParsedCustomId:
    parts = custom_id.split(":")
    if len(parts) < 3 or parts[0] != _CUSTOM_ID_PREFIX:
        raise ValueError(f"Invalid custom_id: {custom_id}")
    action = parts[1]
    interaction_id = int(parts[2])
    qualifier = parts[3] if len(parts) > 3 else None
    if action in _FIX_FIELD_ACTIONS:
        return ParsedCustomId(
            action=action,
            interaction_id=interaction_id,
            field=qualifier,
        )
    if action in _PLAYER_LINK_ACTIONS:
        return ParsedCustomId(
            action=action,
            interaction_id=interaction_id,
            player_slot=int(qualifier) if qualifier is not None else None,
        )
    return ParsedCustomId(
        action=action,
        interaction_id=interaction_id,
        game_id=int(qualifier) if qualifier is not None else None,
    )


def build_game_pick_options(games: list[Game]) -> list[discord.SelectOption]:
    limited = games[:_MAX_GAME_PICK_OPTIONS]
    return [
        discord.SelectOption(label=game.name, value=str(game.id))
        for game in limited
    ]


def build_player_pick_options(players: list[Player]) -> list[discord.SelectOption]:
    humans = [
        player
        for player in players
        if not is_bot_name(player.polytopia_name)
    ]
    limited = humans[:_MAX_PLAYER_PICK_OPTIONS]
    return [
        discord.SelectOption(label=player.polytopia_name, value=str(player.id))
        for player in limited
    ]


class GameEndConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        game_id: int,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.game_id = game_id
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_game_end",
                    interaction_id=interaction_id,
                    game_id=game_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Wrong game",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "reject_game_end",
                    interaction_id=interaction_id,
                ),
            )
        )


class GameEndPickView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        games: list[Game],
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        select = discord.ui.Select(
            placeholder="Which game ended?",
            options=build_game_pick_options(games),
            custom_id=encode_custom_id("pick_game_end", interaction_id=interaction_id),
        )
        self.add_item(select)


class WinRatioConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        other_player_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del other_player_discord_id
        self.interaction_id = interaction_id
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_win_ratio",
                    interaction_id=interaction_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Reject",
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "reject_win_ratio",
                    interaction_id=interaction_id,
                ),
            )
        )


class ExtractionConfirmView(discord.ui.View):
    """Diagnosis preview: Continue / Fix / Abandon (no Confirm — ADR 005)."""

    def __init__(
        self,
        *,
        interaction_id: int,
        uploader_discord_id: str,
        resolved_roster: Sequence[Mapping[str, object]] | None = None,
        slot_confirmations: Mapping[int, bool] | Mapping[str, bool] | None = None,
        fix_resolved_roster_slots: (
            Mapping[int, bool] | Mapping[str, bool] | None
        ) = None,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id, slot_confirmations
        self.interaction_id = interaction_id
        continue_disabled = _continue_review_disabled(
            resolved_roster=resolved_roster,
            fix_resolved_roster_slots=fix_resolved_roster_slots,
        )
        self.add_item(
            discord.ui.Button(
                label="Continue",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "continue_review",
                    interaction_id=interaction_id,
                ),
                disabled=continue_disabled,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Fix",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "fix_extraction",
                    interaction_id=interaction_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Abandon",
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "abandon_staged",
                    interaction_id=interaction_id,
                ),
            )
        )


# Alias preferred by ADR 005 naming; same class.
DiagnosisPreviewView = ExtractionConfirmView


def _continue_review_disabled(
    *,
    resolved_roster: Sequence[Mapping[str, object]] | None,
    fix_resolved_roster_slots: Mapping[int, bool] | Mapping[str, bool] | None,
) -> bool:
    """Continue stays disabled until fuzzy/new slots are Fix-resolved."""
    if not resolved_roster:
        return False
    fix_resolved = fix_resolved_roster_slots or {}
    for index, slot in enumerate(resolved_roster):
        if str(slot.get("match_type", "")) not in {"fuzzy", "new"}:
            continue
        if not _mapping_flag(fix_resolved, index):
            return True
    return False


def _mapping_flag(
    mapping: Mapping[int, bool] | Mapping[str, bool],
    index: int,
) -> bool:
    as_object = dict(mapping)
    value = as_object.get(index, as_object.get(str(index)))
    return bool(value)


class PlayerSpellingConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        polytopia_name: str,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del polytopia_name, uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        self.add_item(
            discord.ui.Button(
                label="Yes",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_player_spelling",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="No, pick different name",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "reject_player_spelling",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )


class PlayerCorrectionPickView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        players: list[Player],
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        select = discord.ui.Select(
            placeholder="Pick the correct Polytopia name",
            options=build_player_pick_options(players),
            custom_id=encode_custom_id(
                "pick_player_correction",
                interaction_id=interaction_id,
                player_slot=player_slot,
            ),
        )
        self.add_item(select)


class PlayerLinkRemoteConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        selected_discord_user_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del selected_discord_user_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_player_link",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Not me",
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "reject_player_link",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )


class PlayerDiscordUserSelectView(discord.ui.View):
    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        select = discord.ui.UserSelect(
            placeholder="Which Discord user is this player?",
            custom_id=encode_custom_id(
                "select_player_discord_user",
                interaction_id=interaction_id,
                player_slot=player_slot,
            ),
            min_values=1,
            max_values=1,
        )
        self.add_item(select)


class ModApprovalView(discord.ui.View):
    def __init__(self, *, interaction_id: int) -> None:
        super().__init__(timeout=None)
        self.interaction_id = interaction_id
        self.add_item(
            discord.ui.Button(
                label="Approve",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "approve_mod_batch",
                    interaction_id=interaction_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Reject",
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "reject_mod_batch",
                    interaction_id=interaction_id,
                ),
            )
        )


class FinalSummaryView(discord.ui.View):
    def __init__(self, *, interaction_id: int) -> None:
        super().__init__(timeout=None)
        self.interaction_id = interaction_id
        self.add_item(
            discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "confirm_final_summary",
                    interaction_id=interaction_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Fix",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "fix_final_summary",
                    interaction_id=interaction_id,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Abandon",
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "abandon_final_summary",
                    interaction_id=interaction_id,
                ),
            )
        )


_GAME_BASICS_CORRECTION_FIELDS = (
    ("game_name", "Game name"),
    ("map_size", "Map size"),
    ("terrain", "Terrain"),
    ("game_timer", "Game timer"),
    ("target_score", "Target score"),
    ("game_type", "Game type"),
)

_GAME_END_CORRECTION_FIELDS = (
    ("game_name", "Game name"),
    ("winner", "Winner"),
)

_REUPLOAD_ONLY_FIELDS = (("reupload", "Re-upload required"),)


class FieldCorrectionView(discord.ui.View):
    """Mobile-first field-correction controls posted by Fix (ADR 005).

    Buttons open modals (handled by the adapter); player-name slots use
    :class:`RosterSlotFixView` instead of a bulk players field.
    """

    def __init__(
        self,
        *,
        interaction_id: int,
        screenshot_type: str,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.screenshot_type = screenshot_type
        for field, label in _correction_fields(screenshot_type):
            self.add_item(
                discord.ui.Button(
                    label=label[:80],
                    style=discord.ButtonStyle.secondary,
                    custom_id=encode_custom_id(
                        "pick_field_correction",
                        interaction_id=interaction_id,
                        field=field,
                    ),
                )
            )


def _correction_fields(screenshot_type: str) -> tuple[tuple[str, str], ...]:
    if screenshot_type == "game_end":
        return _GAME_END_CORRECTION_FIELDS
    if screenshot_type in {"friend_profile", "win_ratio"}:
        return _REUPLOAD_ONLY_FIELDS
    return _GAME_BASICS_CORRECTION_FIELDS


def _correction_field_options(
    screenshot_type: str,
) -> list[discord.SelectOption]:
    """Legacy select options (kept for tests / callers); Fix UI uses buttons."""
    return [
        discord.SelectOption(label=label, value=field)
        for field, label in _correction_fields(screenshot_type)
    ]


def field_label_for(field: str, screenshot_type: str) -> str:
    for name, label in _correction_fields(screenshot_type):
        if name == field:
            return label
    return field.replace("_", " ").title()


def build_field_correction_modal(
    *,
    interaction_id: int,
    field: str,
    label: str,
    current_value: str,
) -> discord.ui.Modal:
    modal = discord.ui.Modal(
        title=f"Fix {label}"[:45],
        custom_id=encode_custom_id(
            "submit_field_correction",
            interaction_id=interaction_id,
            field=field,
        ),
    )
    default = current_value[:400] if current_value else None
    modal.add_item(
        discord.ui.TextInput(
            label=label[:45],
            custom_id="new_value",
            default=default,
            required=True,
            max_length=200,
            style=discord.TextStyle.short,
        )
    )
    return modal


def build_roster_override_modal(
    *,
    interaction_id: int,
    player_slot: int,
    current_name: str,
) -> discord.ui.Modal:
    modal = discord.ui.Modal(
        title="Override player name",
        custom_id=encode_custom_id(
            "submit_roster_override",
            interaction_id=interaction_id,
            player_slot=player_slot,
        ),
    )
    default = current_name[:400] if current_name else None
    modal.add_item(
        discord.ui.TextInput(
            label="Player name",
            custom_id="new_value",
            default=default,
            required=True,
            max_length=100,
            style=discord.TextStyle.short,
        )
    )
    return modal


def modal_text_value(
    interaction: discord.Interaction,
    *,
    custom_id: str = "new_value",
) -> str | None:
    data = interaction.data if isinstance(interaction.data, dict) else None
    if not data:
        return None
    for row in data.get("components", []):
        if not isinstance(row, dict):
            continue
        for component in row.get("components", []):
            if not isinstance(component, dict):
                continue
            if component.get("custom_id") == custom_id:
                value = component.get("value")
                return value if isinstance(value, str) else None
    return None


class RosterSlotFixView(discord.ui.View):
    """Per-slot Fix controls for fuzzy/new roster matches (ADR 005)."""

    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        raw_ocr: str,
        suggested_name: str | None,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id, raw_ocr
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        accept_label = (
            f"Accept suggestion ({suggested_name})"
            if suggested_name
            else "Accept OCR name"
        )
        self.add_item(
            discord.ui.Button(
                label=accept_label[:80],
                style=discord.ButtonStyle.success,
                custom_id=encode_custom_id(
                    "accept_roster_suggestion",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Pick known player",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "pick_roster_known_player",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Override name",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "override_roster_name",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )


class RosterShapeEditView(discord.ui.View):
    """Add-player control posted with Fix (Task 039)."""

    def __init__(
        self,
        *,
        interaction_id: int,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.add_item(
            discord.ui.Button(
                label="Add player",
                style=discord.ButtonStyle.primary,
                custom_id=encode_custom_id(
                    "add_roster_player",
                    interaction_id=interaction_id,
                ),
            )
        )


class RosterHumanShapeView(discord.ui.View):
    """Per-human remove / reorder controls on Fix (Task 039)."""

    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        player_name: str,
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        name_bit = player_name[:24] if player_name else f"slot {player_slot}"
        self.add_item(
            discord.ui.Button(
                label=f"Remove ({name_bit})"[:80],
                style=discord.ButtonStyle.danger,
                custom_id=encode_custom_id(
                    "remove_roster_player",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Move up",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "move_roster_player_up",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Move down",
                style=discord.ButtonStyle.secondary,
                custom_id=encode_custom_id(
                    "move_roster_player_down",
                    interaction_id=interaction_id,
                    player_slot=player_slot,
                ),
            )
        )


def build_add_roster_player_modal(*, interaction_id: int) -> discord.ui.Modal:
    modal = discord.ui.Modal(
        title="Add player",
        custom_id=encode_custom_id(
            "submit_add_roster_player",
            interaction_id=interaction_id,
        ),
    )
    modal.add_item(
        discord.ui.TextInput(
            label="Player name",
            custom_id="new_value",
            required=True,
            max_length=100,
            style=discord.TextStyle.short,
        )
    )
    return modal


class RosterKnownPlayerPickView(discord.ui.View):
    """Known-player picker opened from Fix roster controls."""

    def __init__(
        self,
        *,
        interaction_id: int,
        player_slot: int,
        players: list[Player],
        uploader_discord_id: str,
    ) -> None:
        super().__init__(timeout=None)
        del uploader_discord_id
        self.interaction_id = interaction_id
        self.player_slot = player_slot
        options = build_player_pick_options(players)
        if not options:
            options = [
                discord.SelectOption(
                    label="No known players",
                    value="0",
                )
            ]
        select = discord.ui.Select(
            placeholder="Pick a known Polytopia name",
            options=options,
            custom_id=encode_custom_id(
                "select_roster_known_player",
                interaction_id=interaction_id,
                player_slot=player_slot,
            ),
        )
        self.add_item(select)
