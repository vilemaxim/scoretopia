"""SQLite repository classes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from scoretopia.domain.matching import is_bot_name
from scoretopia.storage.errors import DuplicatePolytopiaNameError
from scoretopia.storage.models import (
    Dispute,
    DisputeCreate,
    Game,
    GameParticipantInput,
    PendingInteraction,
    Player,
    PlayerPairRatio,
    normalize_polytopia_name,
)

_PLAYER_SELECT = """
SELECT id, polytopia_name, discord_user_id, discord_display_name
FROM players
"""

_GAME_SELECT = """
SELECT
    id,
    name,
    status,
    map_size,
    terrain,
    game_type,
    target_score,
    game_timer,
    winner_player_id,
    created_at,
    completed_at
FROM games
"""

_PENDING_SELECT = """
SELECT id, kind, discord_user_id, status, payload
FROM pending_interactions
"""

_DISPUTE_SELECT = """
SELECT
    id,
    player_a_id,
    player_b_id,
    submitter_player_id,
    rejector_player_id,
    claimed_wins_a,
    claimed_wins_b,
    screenshot_path,
    status
FROM disputes
"""


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


class PlayerRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_by_id(self, player_id: int) -> Player | None:
        row = self._conn.execute(
            f"{_PLAYER_SELECT} WHERE id = ?",
            (player_id,),
        ).fetchone()
        return _player_from_row(row) if row else None

    def get_by_polytopia_name(self, polytopia_name: str) -> Player | None:
        normalized = normalize_polytopia_name(polytopia_name)
        row = self._conn.execute(
            f"{_PLAYER_SELECT} WHERE polytopia_name_normalized = ?",
            (normalized,),
        ).fetchone()
        return _player_from_row(row) if row else None

    def get_by_discord_id(self, discord_user_id: str) -> Player | None:
        row = self._conn.execute(
            f"{_PLAYER_SELECT} WHERE discord_user_id = ?",
            (discord_user_id,),
        ).fetchone()
        return _player_from_row(row) if row else None

    def create(
        self,
        *,
        polytopia_name: str,
        discord_user_id: str | None = None,
        discord_display_name: str | None = None,
    ) -> Player:
        normalized = normalize_polytopia_name(polytopia_name)
        if self.get_by_polytopia_name(polytopia_name) is not None:
            raise DuplicatePolytopiaNameError(polytopia_name)

        cursor = self._conn.execute(
            """
            INSERT INTO players (
                polytopia_name,
                polytopia_name_normalized,
                discord_user_id,
                discord_display_name
            )
            VALUES (?, ?, ?, ?)
            """,
            (polytopia_name.strip(), normalized, discord_user_id, discord_display_name),
        )
        self._conn.commit()
        player = self.get_by_id(int(cursor.lastrowid))
        assert player is not None
        return player

    def update_discord_link(
        self,
        player_id: int,
        *,
        discord_user_id: str,
        discord_display_name: str,
    ) -> Player:
        self._conn.execute(
            """
            UPDATE players
            SET discord_user_id = ?, discord_display_name = ?
            WHERE id = ?
            """,
            (discord_user_id, discord_display_name, player_id),
        )
        self._conn.commit()
        player = self.get_by_id(player_id)
        assert player is not None
        return player


class GameRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_active_game(
        self,
        *,
        name: str,
        map_size: int | None = None,
        terrain: str | None = None,
        game_type: str | None = None,
        target_score: int | None = None,
        game_timer: str | None = None,
    ) -> Game:
        cursor = self._conn.execute(
            """
            INSERT INTO games (
                name,
                status,
                map_size,
                terrain,
                game_type,
                target_score,
                game_timer
            )
            VALUES (?, 'active', ?, ?, ?, ?, ?)
            """,
            (name, map_size, terrain, game_type, target_score, game_timer),
        )
        self._conn.commit()
        game = self.get_by_id(int(cursor.lastrowid))
        assert game is not None
        return game

    def get_by_id(self, game_id: int) -> Game | None:
        row = self._conn.execute(
            f"{_GAME_SELECT} WHERE id = ?",
            (game_id,),
        ).fetchone()
        return _game_from_row(row) if row else None

    def list_active(self) -> list[Game]:
        rows = self._conn.execute(
            f"{_GAME_SELECT} WHERE status = 'active' ORDER BY id"
        ).fetchall()
        return [_game_from_row(row) for row in rows]

    def list_completed_since(self, since: datetime) -> list[Game]:
        rows = self._conn.execute(
            f"{_GAME_SELECT} "
            "WHERE status = 'completed' AND completed_at >= ? "
            "ORDER BY completed_at DESC",
            (_format_datetime(since),),
        ).fetchall()
        return [_game_from_row(row) for row in rows]

    def list_active_by_participants(
        self, participant_names: Iterable[str]
    ) -> list[Game]:
        """Return active games whose human participant set exactly matches.

        Bot participants stored on a game are excluded from the game's match key.
        Bot-like names in ``participant_names`` (names ending with `` bot``,
        case-insensitive) are ignored so game-end screenshots that include AI
        players still match the human-only roster from the game-start screenshot.
        """
        query_humans = {
            normalize_polytopia_name(name)
            for name in participant_names
            if not is_bot_name(name)
        }
        matches: list[Game] = []
        for game in self.list_active():
            human_names = self._human_participant_names(game.id)
            if human_names == query_humans:
                matches.append(game)
        return matches

    def _human_participant_names(self, game_id: int) -> set[str]:
        rows = self._conn.execute(
            """
            SELECT p.polytopia_name_normalized
            FROM game_participants gp
            JOIN players p ON p.id = gp.player_id
            WHERE gp.game_id = ? AND gp.is_bot = 0
            ORDER BY p.polytopia_name_normalized
            """,
            (game_id,),
        ).fetchall()
        return {row[0] for row in rows}

    def complete_game(
        self,
        game_id: int,
        winner_player_id: int,
        *,
        completed_at: datetime | None = None,
    ) -> Game:
        completed_at = completed_at or datetime.now(tz=UTC)
        self._conn.execute(
            """
            UPDATE games
            SET status = 'completed',
                winner_player_id = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (winner_player_id, _format_datetime(completed_at), game_id),
        )
        self._conn.commit()
        game = self.get_by_id(game_id)
        assert game is not None
        return game


class GameParticipantRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_participants(
        self,
        game_id: int,
        participants: Sequence[GameParticipantInput],
    ) -> None:
        self._conn.executemany(
            """
            INSERT INTO game_participants (
                game_id,
                player_id,
                tribe,
                score,
                placement,
                is_bot
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    game_id,
                    participant.player_id,
                    participant.tribe,
                    participant.score,
                    participant.placement,
                    int(participant.is_bot),
                )
                for participant in participants
            ],
        )
        self._conn.commit()

    def get_participant_names(self, game_id: int) -> tuple[str, ...]:
        rows = self._conn.execute(
            """
            SELECT p.polytopia_name
            FROM game_participants gp
            JOIN players p ON p.id = gp.player_id
            WHERE gp.game_id = ?
            ORDER BY p.polytopia_name
            """,
            (game_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def get_human_and_bot_count(self, game_id: int) -> tuple[tuple[str, ...], int]:
        rows = self._conn.execute(
            """
            SELECT p.polytopia_name, gp.is_bot
            FROM game_participants gp
            JOIN players p ON p.id = gp.player_id
            WHERE gp.game_id = ?
            ORDER BY p.polytopia_name
            """,
            (game_id,),
        ).fetchall()
        humans: list[str] = []
        bot_count = 0
        for name, is_bot in rows:
            if is_bot:
                bot_count += 1
            else:
                humans.append(str(name))
        return tuple(humans), bot_count

    def update_participant_results(
        self,
        game_id: int,
        results: Sequence[tuple[int, int, int]],
    ) -> None:
        """Update score and placement for participants (player_id, score, placement)."""
        self._conn.executemany(
            """
            UPDATE game_participants
            SET score = ?, placement = ?
            WHERE game_id = ? AND player_id = ?
            """,
            [
                (score, placement, game_id, player_id)
                for player_id, score, placement in results
            ],
        )
        self._conn.commit()


class PendingInteractionRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        *,
        kind: str,
        discord_user_id: str,
        payload: dict[str, object],
    ) -> PendingInteraction:
        cursor = self._conn.execute(
            """
            INSERT INTO pending_interactions (kind, discord_user_id, status, payload)
            VALUES (?, ?, 'open', ?)
            """,
            (kind, discord_user_id, json.dumps(payload)),
        )
        self._conn.commit()
        interaction = self.get_by_id(int(cursor.lastrowid))
        assert interaction is not None
        return interaction

    def get_by_id(self, interaction_id: int) -> PendingInteraction | None:
        row = self._conn.execute(
            f"{_PENDING_SELECT} WHERE id = ?",
            (interaction_id,),
        ).fetchone()
        return _pending_from_row(row) if row else None

    def list_open_for_user(self, discord_user_id: str) -> list[PendingInteraction]:
        rows = self._conn.execute(
            f"{_PENDING_SELECT} "
            "WHERE discord_user_id = ? AND status = 'open' ORDER BY id",
            (discord_user_id,),
        ).fetchall()
        return [_pending_from_row(row) for row in rows]

    def resolve(self, interaction_id: int) -> PendingInteraction:
        self._conn.execute(
            """
            UPDATE pending_interactions
            SET status = 'resolved'
            WHERE id = ?
            """,
            (interaction_id,),
        )
        self._conn.commit()
        interaction = self.get_by_id(interaction_id)
        assert interaction is not None
        return interaction

    def resolve_with_payload(
        self,
        interaction_id: int,
        payload_updates: dict[str, object],
    ) -> PendingInteraction:
        interaction = self.get_by_id(interaction_id)
        assert interaction is not None
        updated_payload = {**interaction.payload, **payload_updates}
        self._conn.execute(
            """
            UPDATE pending_interactions
            SET status = 'resolved', payload = ?
            WHERE id = ?
            """,
            (json.dumps(updated_payload), interaction_id),
        )
        self._conn.commit()
        resolved = self.get_by_id(interaction_id)
        assert resolved is not None
        return resolved

    def list_open_by_kind(self, kind: str) -> list[PendingInteraction]:
        rows = self._conn.execute(
            f"{_PENDING_SELECT} WHERE kind = ? AND status = 'open' ORDER BY id",
            (kind,),
        ).fetchall()
        return [_pending_from_row(row) for row in rows]

    def update_payload(
        self,
        interaction_id: int,
        payload: dict[str, object],
    ) -> PendingInteraction:
        self._conn.execute(
            """
            UPDATE pending_interactions
            SET payload = ?
            WHERE id = ?
            """,
            (json.dumps(payload), interaction_id),
        )
        self._conn.commit()
        interaction = self.get_by_id(interaction_id)
        assert interaction is not None
        return interaction

    def mark_disputed(self, interaction_id: int) -> PendingInteraction:
        self._conn.execute(
            """
            UPDATE pending_interactions
            SET status = 'disputed'
            WHERE id = ?
            """,
            (interaction_id,),
        )
        self._conn.commit()
        interaction = self.get_by_id(interaction_id)
        assert interaction is not None
        return interaction


class PlayerPairRatioRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_ratio(self, player_a_id: int, player_b_id: int) -> PlayerPairRatio | None:
        row = self._conn.execute(
            """
            SELECT player_a_id, player_b_id, wins, source, updated_at
            FROM player_pair_ratios
            WHERE player_a_id = ? AND player_b_id = ?
            """,
            (player_a_id, player_b_id),
        ).fetchone()
        return _ratio_from_row(row) if row else None

    def upsert_ratio(
        self,
        player_a_id: int,
        player_b_id: int,
        *,
        wins: int,
        source: str,
    ) -> PlayerPairRatio:
        self._conn.execute(
            """
            INSERT INTO player_pair_ratios (player_a_id, player_b_id, wins, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(player_a_id, player_b_id) DO UPDATE SET
                wins = excluded.wins,
                source = excluded.source,
                updated_at = datetime('now')
            """,
            (player_a_id, player_b_id, wins, source),
        )
        self._conn.commit()
        ratio = self.get_ratio(player_a_id, player_b_id)
        assert ratio is not None
        return ratio

    def increment_ratio(
        self,
        player_a_id: int,
        player_b_id: int,
        *,
        source: str,
    ) -> PlayerPairRatio:
        existing = self.get_ratio(player_a_id, player_b_id)
        wins = existing.wins + 1 if existing is not None else 1
        return self.upsert_ratio(
            player_a_id,
            player_b_id,
            wins=wins,
            source=source,
        )

    def list_all(self) -> list[PlayerPairRatio]:
        rows = self._conn.execute(
            """
            SELECT player_a_id, player_b_id, wins, source, updated_at
            FROM player_pair_ratios
            ORDER BY player_a_id, player_b_id
            """
        ).fetchall()
        return [_ratio_from_row(row) for row in rows]


class DisputeRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, dispute: DisputeCreate) -> Dispute:
        cursor = self._conn.execute(
            """
            INSERT INTO disputes (
                player_a_id,
                player_b_id,
                submitter_player_id,
                rejector_player_id,
                claimed_wins_a,
                claimed_wins_b,
                screenshot_path,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dispute.player_a_id,
                dispute.player_b_id,
                dispute.submitter_player_id,
                dispute.rejector_player_id,
                dispute.claimed_wins_a,
                dispute.claimed_wins_b,
                dispute.screenshot_path,
                dispute.status,
            ),
        )
        self._conn.commit()
        created = self.get_by_id(int(cursor.lastrowid))
        assert created is not None
        return created

    def get_by_id(self, dispute_id: int) -> Dispute | None:
        row = self._conn.execute(
            f"{_DISPUTE_SELECT} WHERE id = ?",
            (dispute_id,),
        ).fetchone()
        return _dispute_from_row(row) if row else None

    def list_open(self) -> list[Dispute]:
        rows = self._conn.execute(
            f"{_DISPUTE_SELECT} WHERE status = 'open' ORDER BY id"
        ).fetchall()
        return [_dispute_from_row(row) for row in rows]


def _player_from_row(row: tuple[object, ...]) -> Player:
    return Player(
        id=int(row[0]),
        polytopia_name=str(row[1]),
        discord_user_id=row[2],  # type: ignore[arg-type]
        discord_display_name=row[3],  # type: ignore[arg-type]
    )


def _game_from_row(row: tuple[object, ...]) -> Game:
    return Game(
        id=int(row[0]),
        name=str(row[1]),
        status=str(row[2]),
        map_size=row[3],  # type: ignore[arg-type]
        terrain=row[4],  # type: ignore[arg-type]
        game_type=row[5],  # type: ignore[arg-type]
        target_score=row[6],  # type: ignore[arg-type]
        game_timer=row[7],  # type: ignore[arg-type]
        winner_player_id=row[8],  # type: ignore[arg-type]
        created_at=_parse_datetime(row[9]),  # type: ignore[arg-type]
        completed_at=_parse_datetime(row[10]),  # type: ignore[arg-type]
    )


def _pending_from_row(row: tuple[object, ...]) -> PendingInteraction:
    return PendingInteraction(
        id=int(row[0]),
        kind=str(row[1]),
        discord_user_id=str(row[2]),
        status=str(row[3]),
        payload=json.loads(str(row[4])),
    )


def _ratio_from_row(row: tuple[object, ...]) -> PlayerPairRatio:
    return PlayerPairRatio(
        player_a_id=int(row[0]),
        player_b_id=int(row[1]),
        wins=int(row[2]),
        source=str(row[3]),
        updated_at=_parse_datetime(row[4]),  # type: ignore[arg-type]
    )


def _dispute_from_row(row: tuple[object, ...]) -> Dispute:
    return Dispute(
        id=int(row[0]),
        player_a_id=int(row[1]),
        player_b_id=int(row[2]),
        submitter_player_id=int(row[3]),
        rejector_player_id=int(row[4]),
        claimed_wins_a=int(row[5]),
        claimed_wins_b=int(row[6]),
        screenshot_path=row[7],  # type: ignore[arg-type]
        status=str(row[8]),
    )
