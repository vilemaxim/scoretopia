"""Plain-text formatters for report DTOs."""

from __future__ import annotations

from scoretopia.reports.dto import ReportDTO


def format_report_text(dto: ReportDTO) -> str:
    """Render a report DTO as readable plain text for CLI output."""
    lines = [dto.title, dto.description, ""]
    for field in dto.fields:
        lines.append(f"{field.label}: {field.value}")
    if dto.footer:
        if dto.fields:
            lines.append("")
        lines.append(dto.footer)
    return "\n".join(lines)
