"""Report output sinks."""

from __future__ import annotations

from typing import Protocol

from scoretopia.reports.dto import ReportDTO
from scoretopia.reports.format import format_report_text


class ReportPublisher(Protocol):
    """Abstract sink for generated report payloads."""

    def publish(self, report_name: str, dto: ReportDTO, channel_key: str) -> None:
        """Deliver a report to the configured channel."""


class StdoutReportPublisher:
    """Print formatted reports to stdout (CLI and tests)."""

    def publish(self, report_name: str, dto: ReportDTO, channel_key: str) -> None:
        del report_name, channel_key
        text = format_report_text(dto)
        print(text, end="" if text.endswith("\n") else "\n")
