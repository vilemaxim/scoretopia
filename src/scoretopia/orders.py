"""The orders file: what recording tells seat control to do (ADR 003).

Recording rewrites the whole file whenever its state changes and replaces it
in one step, so a reader never sees half a file. Seat control reads it once at
the start of a run. The file is the only channel in this direction: seat
control never opens the database.

Version 1 carries the known players' Polytopia names, which are the players
whose invitations the seat accepts, and a list of games to start that is
always empty for now.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scoretopia.storage.models import Player

logger = logging.getLogger(__name__)

ORDERS_VERSION = 1


class UnknownOrdersVersionError(ValueError):
    """The orders file was written by a version this code does not know."""


@dataclass(frozen=True)
class Orders:
    known_players: tuple[str, ...] = ()
    start_games: tuple[dict[str, object], ...] = ()


def write_orders(path: Path, orders: Orders) -> None:
    """Replace the file at ``path`` with ``orders`` in one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ORDERS_VERSION,
        "known_players": list(orders.known_players),
        "start_games": list(orders.start_games),
    }
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Recording and seat control run as different users. mkstemp creates
        # the file owner-only; the group may read it, so the seat's user reads
        # it by sharing a group with recording. Others may not: it holds names.
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_orders(path: Path) -> Orders:
    """The orders at ``path``; no orders when it is missing or unreadable.

    A file that cannot be read gives empty orders, so a run with a broken file
    accepts nothing. A file that states another version is refused instead: it
    was written by other code, and guessing at its meaning could act on it
    wrongly.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Orders()
    except (OSError, ValueError):
        return _no_orders("unreadable")
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), int):
        return _no_orders("malformed")
    if raw["version"] != ORDERS_VERSION:
        raise UnknownOrdersVersionError(
            f"orders file is version {raw['version']}; this code reads version "
            f"{ORDERS_VERSION}"
        )
    names = raw.get("known_players")
    games = raw.get("start_games", [])
    if (
        not isinstance(names, list)
        or not all(isinstance(name, str) for name in names)
        or not isinstance(games, list)
        or not all(isinstance(game, dict) for game in games)
    ):
        return _no_orders("malformed")
    return Orders(known_players=tuple(names), start_games=tuple(games))


def _no_orders(why: str) -> Orders:
    logger.warning("orders file %s; treating as no orders", why)
    return Orders()


class _PlayerSource(Protocol):
    def list_all(self) -> Sequence[Player]: ...


class OrdersKeeper:
    """Recording's side: writes the orders from what the players table holds."""

    def __init__(self, player_repo: _PlayerSource, path: Path) -> None:
        self._players = player_repo
        self._path = path

    def refresh(self) -> None:
        """Rewrite the orders file from the current known players."""
        known = sorted(
            player.polytopia_name
            for player in self._players.list_all()
            if player.discord_user_id
        )
        write_orders(self._path, Orders(known_players=tuple(known)))

    def refresh_or_log(self) -> None:
        """Refresh; a failed write is logged, not raised.

        What prompted the refresh is already saved, and the next refresh
        writes the whole file again, so failing the caller would cost more
        than it protects: recording keeps running with stale orders, under
        which the seat only does less.
        """
        try:
            self.refresh()
        except OSError:
            logger.exception(
                "orders file not written; the next change or restart rewrites it"
            )
