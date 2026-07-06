"""Tests for win-ratio confirmation and dispute flagging (Task 009)."""

from __future__ import annotations

import sqlite3

import pytest

from scoretopia.domain.win_ratios import (
    ConfirmOutcome,
    ConfirmResult,
    DisputeResult,
    PendingWinRatio,
    WinRatioService,
)
from scoretopia.screenshot.models import FriendProfileExtraction, WinRatio
from scoretopia.storage.db import open_database
from scoretopia.storage.repos import (
    DisputeRepo,
    PendingInteractionRepo,
    PlayerPairRatioRepo,
    PlayerRepo,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = open_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def player_repo(conn: sqlite3.Connection) -> PlayerRepo:
    return PlayerRepo(conn)


@pytest.fixture
def pending_repo(conn: sqlite3.Connection) -> PendingInteractionRepo:
    return PendingInteractionRepo(conn)


@pytest.fixture
def ratio_repo(conn: sqlite3.Connection) -> PlayerPairRatioRepo:
    return PlayerPairRatioRepo(conn)


@pytest.fixture
def dispute_repo(conn: sqlite3.Connection) -> DisputeRepo:
    return DisputeRepo(conn)


@pytest.fixture
def win_ratio_service(
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
    dispute_repo: DisputeRepo,
) -> WinRatioService:
    return WinRatioService(
        player_repo,
        pending_repo,
        ratio_repo,
        dispute_repo,
    )


def _friend_profile_extraction(
    *,
    friend_name: str,
    you_wins: int,
    friend_wins: int,
    you_name: str | None = None,
) -> FriendProfileExtraction:
    return FriendProfileExtraction(
        friend_name=friend_name,
        win_ratio=WinRatio(
            you_name=you_name,
            you_wins=you_wins,
            friend_name=friend_name,
            friend_wins=friend_wins,
        ),
    )


def test_submit_from_screenshot_creates_pending_for_other_player(
    win_ratio_service: WinRatioService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
) -> None:
    submitter = player_repo.create(
        polytopia_name="Alice",
        discord_user_id="discord-alice",
        discord_display_name="alice-discord",
    )
    friend = player_repo.create(polytopia_name="Bob")
    extraction = _friend_profile_extraction(
        friend_name=friend.polytopia_name,
        you_wins=10,
        friend_wins=7,
        you_name=submitter.polytopia_name,
    )

    result = win_ratio_service.submit_from_screenshot(
        extraction,
        submitter_discord_id="discord-alice",
        screenshot_path="/inbox/alice-bob-ratio.png",
    )

    assert isinstance(result, PendingWinRatio)
    assert result.interaction_id > 0
    assert result.other_player_id == friend.id

    pending = pending_repo.get_by_id(result.interaction_id)
    assert pending is not None
    assert pending.kind == "win_ratio_needs_confirmation"
    assert pending.discord_user_id == "discord-alice"
    assert pending.status == "open"
    assert pending.payload["other_player_id"] == friend.id
    assert pending.payload["you_wins"] == 10
    assert pending.payload["friend_wins"] == 7
    assert pending.payload["screenshot_path"] == "/inbox/alice-bob-ratio.png"


def test_confirm_by_other_player_updates_pair_ratios_from_screenshot(
    win_ratio_service: WinRatioService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> None:
    submitter = player_repo.create(
        polytopia_name="Carol",
        discord_user_id="discord-carol",
    )
    friend = player_repo.create(
        polytopia_name="Dave",
        discord_user_id="discord-dave",
    )
    pending = win_ratio_service.submit_from_screenshot(
        _friend_profile_extraction(
            friend_name=friend.polytopia_name,
            you_wins=16,
            friend_wins=22,
        ),
        submitter_discord_id="discord-carol",
        screenshot_path="/inbox/carol-dave-ratio.png",
    )

    result = win_ratio_service.confirm(
        pending.interaction_id,
        confirmer_discord_id="discord-dave",
    )

    assert isinstance(result, ConfirmResult)
    assert result.outcome == ConfirmOutcome.SUCCESS

    carol_vs_dave = ratio_repo.get_ratio(submitter.id, friend.id)
    dave_vs_carol = ratio_repo.get_ratio(friend.id, submitter.id)
    assert carol_vs_dave is not None
    assert carol_vs_dave.wins == 16
    assert carol_vs_dave.source == "screenshot"
    assert dave_vs_carol is not None
    assert dave_vs_carol.wins == 22
    assert dave_vs_carol.source == "screenshot"

    resolved = pending_repo.get_by_id(pending.interaction_id)
    assert resolved is not None
    assert resolved.status == "resolved"


def test_confirm_overwrites_prior_computed_ratios(
    win_ratio_service: WinRatioService,
    player_repo: PlayerRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> None:
    submitter = player_repo.create(
        polytopia_name="Eve",
        discord_user_id="discord-eve",
    )
    friend = player_repo.create(
        polytopia_name="Frank",
        discord_user_id="discord-frank",
    )
    ratio_repo.upsert_ratio(submitter.id, friend.id, wins=3, source="computed")
    ratio_repo.upsert_ratio(friend.id, submitter.id, wins=5, source="computed")

    pending = win_ratio_service.submit_from_screenshot(
        _friend_profile_extraction(
            friend_name=friend.polytopia_name,
            you_wins=12,
            friend_wins=8,
        ),
        submitter_discord_id="discord-eve",
        screenshot_path="/inbox/eve-frank-ratio.png",
    )

    result = win_ratio_service.confirm(
        pending.interaction_id,
        confirmer_discord_id="discord-frank",
    )

    assert result.outcome == ConfirmOutcome.SUCCESS
    eve_vs_frank = ratio_repo.get_ratio(submitter.id, friend.id)
    frank_vs_eve = ratio_repo.get_ratio(friend.id, submitter.id)
    assert eve_vs_frank is not None
    assert eve_vs_frank.wins == 12
    assert eve_vs_frank.source == "screenshot"
    assert frank_vs_eve is not None
    assert frank_vs_eve.wins == 8
    assert frank_vs_eve.source == "screenshot"


def test_confirm_by_submitter_returns_not_authorized(
    win_ratio_service: WinRatioService,
    player_repo: PlayerRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> None:
    submitter = player_repo.create(
        polytopia_name="Grace",
        discord_user_id="discord-grace",
    )
    friend = player_repo.create(
        polytopia_name="Henry",
        discord_user_id="discord-henry",
    )
    pending = win_ratio_service.submit_from_screenshot(
        _friend_profile_extraction(
            friend_name=friend.polytopia_name,
            you_wins=4,
            friend_wins=6,
        ),
        submitter_discord_id="discord-grace",
        screenshot_path="/inbox/grace-henry-ratio.png",
    )

    result = win_ratio_service.confirm(
        pending.interaction_id,
        confirmer_discord_id="discord-grace",
    )

    assert result == ConfirmResult.not_authorized()
    assert ratio_repo.get_ratio(submitter.id, friend.id) is None


def test_confirm_by_third_party_returns_not_authorized(
    win_ratio_service: WinRatioService,
    player_repo: PlayerRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> None:
    submitter = player_repo.create(
        polytopia_name="Ivy",
        discord_user_id="discord-ivy",
    )
    friend = player_repo.create(
        polytopia_name="Jack",
        discord_user_id="discord-jack",
    )
    player_repo.create(
        polytopia_name="Karen",
        discord_user_id="discord-karen",
    )
    pending = win_ratio_service.submit_from_screenshot(
        _friend_profile_extraction(
            friend_name=friend.polytopia_name,
            you_wins=2,
            friend_wins=3,
        ),
        submitter_discord_id="discord-ivy",
        screenshot_path="/inbox/ivy-jack-ratio.png",
    )

    result = win_ratio_service.confirm(
        pending.interaction_id,
        confirmer_discord_id="discord-karen",
    )

    assert result == ConfirmResult.not_authorized()
    assert ratio_repo.get_ratio(submitter.id, friend.id) is None


def test_reject_creates_open_dispute_and_returns_channel_message_dto(
    win_ratio_service: WinRatioService,
    player_repo: PlayerRepo,
    pending_repo: PendingInteractionRepo,
    dispute_repo: DisputeRepo,
) -> None:
    submitter = player_repo.create(
        polytopia_name="Leo",
        discord_user_id="discord-leo",
    )
    friend = player_repo.create(
        polytopia_name="Mia",
        discord_user_id="discord-mia",
    )
    screenshot_path = "/inbox/leo-mia-ratio.png"
    pending = win_ratio_service.submit_from_screenshot(
        _friend_profile_extraction(
            friend_name=friend.polytopia_name,
            you_wins=9,
            friend_wins=11,
        ),
        submitter_discord_id="discord-leo",
        screenshot_path=screenshot_path,
    )

    result = win_ratio_service.reject(
        pending.interaction_id,
        confirmer_discord_id="discord-mia",
        reason="Those numbers look wrong",
    )

    assert isinstance(result, DisputeResult)
    assert result.dispute_id > 0
    assert result.action == "win_ratio_disputed"
    assert "Leo" in result.message
    assert "Mia" in result.message
    assert "Those numbers look wrong" in result.message

    dispute = dispute_repo.get_by_id(result.dispute_id)
    assert dispute is not None
    assert dispute.status == "open"
    assert dispute.submitter_player_id == submitter.id
    assert dispute.rejector_player_id == friend.id
    assert dispute.claimed_wins_a == 9
    assert dispute.claimed_wins_b == 11
    assert dispute.screenshot_path == screenshot_path

    marked = pending_repo.get_by_id(pending.interaction_id)
    assert marked is not None
    assert marked.status == "disputed"


def test_reconfirmed_screenshot_after_dispute_overwrites_ratio(
    win_ratio_service: WinRatioService,
    player_repo: PlayerRepo,
    ratio_repo: PlayerPairRatioRepo,
) -> None:
    """After a dispute, a new confirmed screenshot still wins over stored ratios."""
    submitter = player_repo.create(
        polytopia_name="Nina",
        discord_user_id="discord-nina",
    )
    friend = player_repo.create(
        polytopia_name="Oscar",
        discord_user_id="discord-oscar",
    )
    ratio_repo.upsert_ratio(submitter.id, friend.id, wins=1, source="computed")
    ratio_repo.upsert_ratio(friend.id, submitter.id, wins=1, source="computed")

    first = win_ratio_service.submit_from_screenshot(
        _friend_profile_extraction(
            friend_name=friend.polytopia_name,
            you_wins=5,
            friend_wins=6,
        ),
        submitter_discord_id="discord-nina",
        screenshot_path="/inbox/nina-oscar-v1.png",
    )
    win_ratio_service.reject(
        first.interaction_id,
        confirmer_discord_id="discord-oscar",
        reason="Incorrect",
    )

    second = win_ratio_service.submit_from_screenshot(
        _friend_profile_extraction(
            friend_name=friend.polytopia_name,
            you_wins=14,
            friend_wins=10,
        ),
        submitter_discord_id="discord-nina",
        screenshot_path="/inbox/nina-oscar-v2.png",
    )
    result = win_ratio_service.confirm(
        second.interaction_id,
        confirmer_discord_id="discord-oscar",
    )

    assert result.outcome == ConfirmOutcome.SUCCESS
    nina_vs_oscar = ratio_repo.get_ratio(submitter.id, friend.id)
    oscar_vs_nina = ratio_repo.get_ratio(friend.id, submitter.id)
    assert nina_vs_oscar is not None
    assert nina_vs_oscar.wins == 14
    assert nina_vs_oscar.source == "screenshot"
    assert oscar_vs_nina is not None
    assert oscar_vs_nina.wins == 10
    assert oscar_vs_nina.source == "screenshot"
