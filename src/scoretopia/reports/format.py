"""Plain-text formatters for report DTOs."""

from __future__ import annotations

from scoretopia.reports.dto import ReportDTO
from scoretopia.reports.kinds import ReportKind


def format_report_text(dto: ReportDTO) -> str:
    """Render a report DTO as readable plain text for CLI output."""
    lines = [dto.title, dto.description, ""]
    if dto.kind is ReportKind.active_games:
        for index, field in enumerate(dto.fields):
            if index > 0:
                lines.append("")
            lines.append(field.label)
            lines.extend(field.value.split("\n"))
    else:
        for field in dto.fields:
            lines.append(f"{field.label}: {field.value}")
    if dto.footer:
        if dto.fields:
            lines.append("")
        lines.append(dto.footer)
    return "\n".join(lines)
