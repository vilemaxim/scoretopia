"""Read-only report generators backed by storage repositories."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from scoretopia.reports.dto import ReportDTO, ReportField
from scoretopia.reports.game_settings import settings_summary
from scoretopia.reports.kinds import ReportKind
from scoretopia.storage.models import Game
from scoretopia.storage.repos import (
    GameParticipantRepo,
    GameRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)

_FIELD_SEP = " · "


class ReportService:
    def __init__(
        self,
        game_repo: GameRepo,
        participant_repo: GameParticipantRepo,
        player_repo: PlayerRepo,
        ratio_repo: PlayerPairRatioRepo,
    ) -> None:
        self._game_repo = game_repo
        self._participant_repo = participant_repo
        self._player_repo = player_repo
        self._ratio_repo = ratio_repo

    def active_games(self) -> ReportDTO:
        games = self._game_repo.list_active()
        if not games:
            return self._empty_report("Active Games", "No active games.")

        fields = [self._active_game_field(game) for game in games]
        return ReportDTO(
            title="Active Games",
            description=f"{len(fields)} game(s) currently in progress.",
            fields=fields,
            kind=ReportKind.active_games,
        )

    def recent_completions(self, lookback_days: int) -> ReportDTO:
        cutoff = datetime.now(tz=UTC) - timedelta(days=lookback_days)
        games = self._game_repo.list_completed_since(cutoff)
        if not games:
            return self._empty_report("Recent Completions", "No recent completions.")

        fields = [self._completed_game_field(game) for game in games]
        return ReportDTO(
            title="Recent Completions",
            description=(
                f"{len(fields)} game(s) completed in the last {lookback_days} day(s)."
            ),
            fields=fields,
            kind=ReportKind.recent_completions,
        )

    def win_ratios(self) -> ReportDTO:
        ratios = self._ratio_repo.list_all()
        if not ratios:
            return self._empty_report(
                "Win Ratios",
                "No win ratio data recorded yet.",
            )

        wins_by_pair = {
            (ratio.player_a_id, ratio.player_b_id): ratio.wins for ratio in ratios
        }
        opponents_by_player: dict[int, set[int]] = defaultdict(set)
        for player_a_id, player_b_id in wins_by_pair:
            opponents_by_player[player_a_id].add(player_b_id)
            opponents_by_player[player_b_id].add(player_a_id)

        player_names = self._player_names(opponents_by_player.keys())
        fields = [
            ReportField(
                label=player_names[player_id],
                value=_FIELD_SEP.join(
                    self._opponent_record(
                        player_id,
                        opponent_id,
                        player_names,
                        wins_by_pair,
                    )
                    for opponent_id in sorted(opponents_by_player[player_id])
                ),
            )
            for player_id in sorted(player_names)
        ]

        return ReportDTO(
            title="Win Ratios",
            description=f"Head-to-head records for {len(fields)} player(s).",
            fields=fields,
            kind=ReportKind.win_ratios,
        )

    def _empty_report(self, title: str, description: str) -> ReportDTO:
        return ReportDTO(title=title, description=description, fields=[])

    def _player_names(self, player_ids: set[int] | list[int]) -> dict[int, str]:
        names: dict[int, str] = {}
        for player_id in player_ids:
            player = self._player_repo.get_by_id(player_id)
            if player is not None:
                names[player_id] = player.polytopia_name
        return names

    def _opponent_record(
        self,
        player_id: int,
        opponent_id: int,
        player_names: dict[int, str],
        wins_by_pair: dict[tuple[int, int], int],
    ) -> str:
        my_wins = wins_by_pair.get((player_id, opponent_id), 0)
        their_wins = wins_by_pair.get((opponent_id, player_id), 0)
        return f"{player_names[opponent_id]}: {my_wins}-{their_wins}"

    def _active_game_field(self, game: Game) -> ReportField:
        value_parts = [self._participants_text(game.id)]
        if started := self._format_datetime(game.created_at):
            value_parts.append(f"Started {started}")
        if settings := settings_summary(game):
            value_parts.append(settings)
        return ReportField(label=game.name, value=_FIELD_SEP.join(value_parts))

    def _completed_game_field(self, game: Game) -> ReportField:
        winner_name = self._winner_name(game)
        value_parts = [f"Winner: {winner_name}", self._participants_text(game.id)]
        if completed := self._format_datetime(game.completed_at):
            value_parts.append(f"Completed {completed}")
        return ReportField(label=game.name, value=_FIELD_SEP.join(value_parts))

    def _participants_text(self, game_id: int) -> str:
        humans, bot_count = self._participant_repo.get_human_and_bot_count(game_id)
        parts: list[str] = []
        if humans:
            parts.append(", ".join(humans))
        if bot_count > 0:
            parts.append(f"Bots: {bot_count}")
        return _FIELD_SEP.join(parts)

    def _winner_name(self, game: Game) -> str:
        if game.winner_player_id is None:
            return "Unknown"
        winner = self._player_repo.get_by_id(game.winner_player_id)
        return winner.polytopia_name if winner is not None else "Unknown"

    def _format_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.strftime("%Y-%m-%d")
