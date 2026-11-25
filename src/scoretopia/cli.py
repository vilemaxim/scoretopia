"""Scoretopia command-line interface."""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from scoretopia.config import (
    KNOWN_REPORTS,
    ConfigError,
    ScoretopiaConfig,
    load_config,
)
from scoretopia.discord.adapter import DiscordBotAdapter, load_discord_token
from scoretopia.domain.captures import CaptureIngestService, seed_captures
from scoretopia.domain.games import GameService
from scoretopia.domain.players import PlayerService
from scoretopia.orders import OrdersKeeper
from scoretopia.reports.publisher import StdoutReportPublisher
from scoretopia.reports.scheduler import (
    run_due_reports,
    run_report,
    run_scheduler_loop,
)
from scoretopia.reports.service import ReportService
from scoretopia.seat import commands as seat_commands
from scoretopia.seat.config import SeatConfig, load_seat_config
from scoretopia.seat.seam import Seat, live_seat
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import (
    CaptureManifestRepo,
    GameParticipantRepo,
    GameRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)


def _build_report_service(
    config: ScoretopiaConfig,
) -> tuple[sqlite3.Connection, ReportService]:
    conn = open_database(str(config.database.path))
    service = ReportService(
        GameRepo(conn),
        GameParticipantRepo(conn),
        PlayerRepo(conn),
        PlayerPairRatioRepo(conn),
    )
    return conn, service


@contextmanager
def _report_session(
    config_path: Path | None,
) -> Iterator[tuple[ScoretopiaConfig, ReportService]]:
    config = load_config(config_path)
    conn, service = _build_report_service(config)
    try:
        yield config, service
    finally:
        conn.close()


def _cmd_report_run(args: argparse.Namespace) -> None:
    if args.name is not None and args.name not in KNOWN_REPORTS:
        raise ValueError(f"Unknown report name: {args.name}")

    with _report_session(args.config) as (config, service):
        publisher = StdoutReportPublisher()
        if args.name is not None:
            run_report(args.name, config, service, publisher)
            return
        run_due_reports(config, service, publisher, force_all=args.all)


def _cmd_report_schedule(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with _report_session(args.config) as (config, service):
        run_scheduler_loop(config, service, StdoutReportPublisher())


def _build_capture_service(
    config: ScoretopiaConfig,
    conn: sqlite3.Connection,
) -> CaptureIngestService:
    from scoretopia.screenshot.captures import OcrCaptureExtractor

    game_repo = GameRepo(conn)
    game_service = GameService(
        game_repo,
        GameParticipantRepo(conn),
        PlayerRepo(conn),
        ratio_repo=PlayerPairRatioRepo(conn),
    )
    return CaptureIngestService(
        captures_path=config.captures.path,
        capture_repo=CaptureManifestRepo(conn),
        game_repo=game_repo,
        game_service=game_service,
        extractor=OcrCaptureExtractor(),
        player_repo=PlayerRepo(conn),
    )


def _cmd_captures_run(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    config.database.path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(str(config.database.path))
    try:
        service = _build_capture_service(config, conn)
        posted = service.run_once(StdoutReportPublisher(), limit=args.limit)
        remaining = service.remaining()
    finally:
        conn.close()
    print(f"Captures processed: {posted} posted", file=sys.stderr)
    print(f"Captures remaining: {remaining}", file=sys.stderr)


def _cmd_captures_seed(args: argparse.Namespace) -> None:
    """Mark every capture already on disk processed, without OCR or posts."""
    config = load_config(args.config)
    config.database.path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(str(config.database.path))
    try:
        seeded = seed_captures(config.captures.path, CaptureManifestRepo(conn))
    finally:
        conn.close()
    print(f"Captures seeded: {seeded}", file=sys.stderr)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer: {value}")
    return number


def _build_player_service(
    config: ScoretopiaConfig,
    player_repo: PlayerRepo,
) -> PlayerService:
    """The player service that keeps the orders file current.

    Writes the orders once now, and again after every change to who is
    registered.
    """
    orders = OrdersKeeper(player_repo, config.orders.path)
    orders.refresh_or_log()
    return PlayerService(player_repo, on_players_changed=orders.refresh_or_log)


def _build_discord_bot(config: ScoretopiaConfig, token: str) -> DiscordBotAdapter:
    config.database.path.parent.mkdir(parents=True, exist_ok=True)

    conn = open_database(str(config.database.path))
    player_repo = PlayerRepo(conn)

    return DiscordBotAdapter(
        config=config,
        player_service=_build_player_service(config, player_repo),
        report_service=ReportService(
            GameRepo(conn),
            GameParticipantRepo(conn),
            player_repo,
            PlayerPairRatioRepo(conn),
        ),
        token=token,
        capture_service=_build_capture_service(
            config,
            open_database(str(config.database.path), check_same_thread=False),
        ),
        capture_poll_seconds=config.captures.poll_seconds,
    )


def _cmd_bot(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    token = load_discord_token()
    adapter = _build_discord_bot(config, token)
    adapter.run()


SeatCommand = Callable[[Seat, SeatConfig], seat_commands.SeatResult]


def _run_seat_command(
    args: argparse.Namespace, action: str, command: SeatCommand
) -> int:
    """Run one seat command on the live client; print one line of JSON."""
    try:
        config = load_seat_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        result = seat_commands.SeatResult(
            action, seat_commands.Outcome.FAILED, code="config_error"
        )
    else:
        try:
            with live_seat(
                display=config.display,
                x_user=config.x_user,
                budget_seconds=config.budget_seconds,
            ) as seat:
                result = command(seat, config)
        except seat_commands.SEAT_ERRORS as error:
            result = seat_commands.failure(action, error)
    print(result.line())
    return result.exit_code


def _cmd_seat_main_menu(args: argparse.Namespace) -> int:
    """Return the game client to the main menu."""
    return _run_seat_command(
        args,
        seat_commands.MAIN_MENU,
        lambda seat, config: seat_commands.main_menu(seat, config.frames_path),
    )


def _cmd_seat_capture_finished(args: argparse.Namespace) -> int:
    """Capture every finished game not yet captured."""
    # Captures are read by recording through a shared group, and by no one
    # else.
    os.umask(0o027)
    return _run_seat_command(
        args,
        seat_commands.CAPTURE_FINISHED,
        lambda seat, config: seat_commands.capture_finished(
            seat, config.captures_path, config.frames_path
        ),
    )


def _cmd_seat_capture_setup(args: argparse.Namespace) -> int:
    """Capture the setup of every started game not yet captured."""
    os.umask(0o027)  # As capture-finished: for the owner and the group only.
    return _run_seat_command(
        args,
        seat_commands.CAPTURE_SETUP,
        lambda seat, config: seat_commands.capture_setup(
            seat, config.captures_path, config.frames_path
        ),
    )


def _config_parent_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to scoretopia.yaml (default: config/scoretopia.yaml)",
    )
    return parent


def main(argv: Sequence[str] | None = None) -> None:
    config_parent = _config_parent_parser()
    parser = argparse.ArgumentParser(description="Scoretopia Polytopia game tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report", help="Generate or schedule reports")
    report_sub = report_parser.add_subparsers(dest="report_command", required=True)

    run_parser = report_sub.add_parser(
        "run",
        parents=[config_parent],
        help="Run reports on demand",
    )
    run_parser.add_argument(
        "--name",
        help="Run a single report by name (e.g. active_games)",
    )
    run_parser.add_argument(
        "--all",
        action="store_true",
        help="Run all enabled reports regardless of cron schedule",
    )
    run_parser.set_defaults(handler=_cmd_report_run)

    schedule_parser = report_sub.add_parser(
        "schedule",
        parents=[config_parent],
        help="Long-running scheduler that runs reports when cron is due",
    )
    schedule_parser.set_defaults(handler=_cmd_report_schedule)

    captures_parser = subparsers.add_parser(
        "captures",
        help="Process steam-test capture directories",
    )
    captures_sub = captures_parser.add_subparsers(
        dest="captures_command",
        required=True,
    )
    captures_run_parser = captures_sub.add_parser(
        "run",
        parents=[config_parent],
        help=(
            "Process captures.path once and print each game start and end "
            "(no Discord); for testing"
        ),
    )
    captures_run_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Process at most N unprocessed capture directories, then print how "
            "many remain (default: all)"
        ),
    )
    captures_run_parser.set_defaults(handler=_cmd_captures_run)

    captures_seed_parser = captures_sub.add_parser(
        "seed",
        parents=[config_parent],
        help=(
            "Mark every complete capture directory under captures.path processed "
            "without reading it or posting"
        ),
    )
    captures_seed_parser.set_defaults(handler=_cmd_captures_seed)

    bot_parser = subparsers.add_parser(
        "bot",
        parents=[config_parent],
        help="Run the Discord gateway bot (long-running process)",
    )
    bot_parser.set_defaults(handler=_cmd_bot)

    seat_parser = subparsers.add_parser(
        "seat",
        help="Control the seat's game client (one action per command)",
    )
    seat_sub = seat_parser.add_subparsers(dest="seat_command", required=True)
    seat_config_parent = argparse.ArgumentParser(add_help=False)
    seat_config_parent.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to seat control's own config (default: config/seat.yaml)",
    )
    exit_codes = (
        "Prints one line of JSON; exits 0 succeeded, 10 nothing to do, "
        "20 unknown screen, 1 other failure"
    )
    seat_sub.add_parser(
        "main-menu",
        parents=[seat_config_parent],
        help=f"Return the client to the main menu. {exit_codes}",
    ).set_defaults(handler=_cmd_seat_main_menu)
    seat_sub.add_parser(
        "capture-finished",
        parents=[seat_config_parent],
        help=(
            f"Capture the screens of every finished game not yet captured. {exit_codes}"
        ),
    ).set_defaults(handler=_cmd_seat_capture_finished)
    seat_sub.add_parser(
        "capture-setup",
        parents=[seat_config_parent],
        help=(
            "Capture the setup of every started game not yet captured. "
            f"{exit_codes}"
        ),
    ).set_defaults(handler=_cmd_seat_capture_setup)

    args = parser.parse_args(argv)
    try:
        exit_code = args.handler(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise
    if exit_code is not None:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
