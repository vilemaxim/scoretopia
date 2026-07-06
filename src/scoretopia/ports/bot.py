"""Platform-agnostic bot entrypoint protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BotPort(Protocol):
    """Long-running bot process contract."""

    def run(self) -> None:
        """Start the bot and block until shutdown."""
