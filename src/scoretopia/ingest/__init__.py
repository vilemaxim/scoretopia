"""Structured ingest logging namespace (ADR 002).

INFO lines cover screenshot summary, participant rosters, match outcomes, and
pending interaction ids. Full extraction payloads are logged at DEBUG only.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
