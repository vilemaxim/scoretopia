"""Scheduled and on-demand report execution."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from croniter import croniter

from scoretopia.config import KNOWN_REPORTS, ReportConfig, ScoretopiaConfig
from scoretopia.reports.dto import ReportDTO
from scoretopia.reports.publisher import ReportPublisher
from scoretopia.reports.service import ReportService

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(tz=UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current


def is_cron_due(schedule: str, now: datetime) -> bool:
    """Return whether ``schedule`` fires in the minute containing ``now``."""
    current = _utc_now(now)
    base = current - timedelta(seconds=1)
    next_fire = croniter(schedule, base).get_next(datetime)
    return next_fire.replace(second=0, microsecond=0) == current.replace(
        second=0, microsecond=0
    )


def generate_report(
    name: str,
    service: ReportService,
    report_config: ReportConfig,
) -> ReportDTO:
    if name not in KNOWN_REPORTS:
        raise ValueError(f"Unknown report name: {name}")
    match name:
        case "active_games":
            return service.active_games()
        case "recent_completions":
            lookback = report_config.lookback_days or 14
            return service.recent_completions(lookback)
        case "win_ratios":
            return service.win_ratios()
        case _:
            raise ValueError(f"Unknown report name: {name}")


def run_report(
    name: str,
    config: ScoretopiaConfig,
    service: ReportService,
    publisher: ReportPublisher,
    *,
    now: datetime | None = None,
) -> bool:
    """Generate and publish one report. Returns True on success."""
    timestamp = _utc_now(now)
    report_config = config.reports[name]
    try:
        dto = generate_report(name, service, report_config)
        publisher.publish(name, dto, report_config.channel)
    except Exception:
        logger.exception(
            "Report %s at %s: failure",
            name,
            timestamp.isoformat(),
        )
        return False
    logger.info(
        "Report %s at %s: success",
        name,
        timestamp.isoformat(),
    )
    return True


def run_due_reports(
    config: ScoretopiaConfig,
    service: ReportService,
    publisher: ReportPublisher,
    *,
    now: datetime | None = None,
    force_all: bool = False,
) -> list[str]:
    """Run enabled reports that are due (or all enabled when ``force_all``)."""
    current = _utc_now(now)
    ran: list[str] = []
    for name, report_config in sorted(config.reports.items()):
        if not report_config.enabled:
            continue
        if not force_all and not is_cron_due(report_config.schedule, current):
            continue
        run_report(name, config, service, publisher, now=current)
        ran.append(name)
    return ran


def run_scheduler_tick(
    config: ScoretopiaConfig,
    service: ReportService,
    publisher: ReportPublisher,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Execute one scheduler cycle: run all reports due at ``now``."""
    return run_due_reports(config, service, publisher, now=now)


def seconds_until_next_due(
    config: ScoretopiaConfig,
    now: datetime | None = None,
) -> float:
    """Seconds until the next enabled report cron fires."""
    current = _utc_now(now)
    next_times = [
        croniter(report_config.schedule, current).get_next(datetime)
        for report_config in config.reports.values()
        if report_config.enabled
    ]
    if not next_times:
        return 60.0
    earliest = min(next_times)
    return max(0.0, (earliest - current).total_seconds())


def run_scheduler_loop(
    config: ScoretopiaConfig,
    service: ReportService,
    publisher: ReportPublisher,
    *,
    sleep: Callable[[float], None] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> None:
    """Long-running loop: sleep until the next due report, then execute."""
    sleep_fn = sleep or time.sleep
    clock = now_fn or (lambda: datetime.now(tz=UTC))
    while True:
        now = clock()
        run_scheduler_tick(config, service, publisher, now=now)
        sleep_fn(seconds_until_next_due(config, now))
