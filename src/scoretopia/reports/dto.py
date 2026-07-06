"""Platform-agnostic report data transfer objects."""

from __future__ import annotations

from dataclasses import dataclass

from scoretopia.reports.kinds import ReportKind


@dataclass(frozen=True)
class ReportField:
    """One row in a report, mappable to Discord embed fields."""

    label: str
    value: str


@dataclass(frozen=True)
class ReportDTO:
    """Structured report payload with no platform-specific types."""

    title: str
    description: str
    fields: list[ReportField]
    footer: str | None = None
    kind: ReportKind | None = None
