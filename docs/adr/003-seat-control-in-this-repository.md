# ADR 003: Seat control lives in this repository, with files as the seam to recording

**Status:** Accepted  
**Date:** 2026-11-23

The code that recognises Polytopia's screens and drives the game client grew up
inside infrastructure playbooks in a separate, private repository, with no
tests. We decided to move all of it here as the `scoretopia.seat` package,
because Scoretopia is now responsible for controlling its seat, and because the
code can only be tested from recorded frames once it is an importable package.

Seat control and recording stay two separate processes, run as different users,
with files between them in both directions:

- **Captures** flow from seat control to recording: frames of a game plus a
  manifest, as today.
- **Orders** flow from recording to seat control: which known players to accept
  invitations from, and which games to start with which players and settings.

Seat control therefore never holds the Discord token or the database. A failed
read can be retried from the saved frames, and saved frames double as test
recordings.

The infrastructure playbooks keep only what is infrastructure: installing Steam
and the game, deploying Scoretopia, and scheduling runs. They call
`scoretopia seat <action>` commands, each of which prints a one-line JSON result
and uses its exit code to say whether it succeeded, had nothing to do, or
stopped on a screen it did not recognise. This repository owns that command
surface and the formats of captures and orders.

## Considered options

- **Leave the code with the playbooks and test it there.** Rejected: it keeps
  the logic inline in playbooks, and splits one responsibility across two
  repositories.
- **Split it: recognisers here, click sequences there.** Rejected: screen
  coordinates and the code that clicks them would live in different
  repositories and drift.
- **One process that drives, reads, and posts.** Rejected: it puts the Discord
  token and database in the process that controls a game client, and loses the
  saved frames as a retry point and test corpus.
- **Seat control reads the database directly, or takes names as command
  arguments.** Rejected in favour of orders: a file can be inspected and
  replayed in tests, and keeps names off command lines.

## Consequences

- The code becomes public. Every file is read for private details (paths,
  machine identifiers, names) before it lands here.
- Seat control may use the OCR already in this repository, to read a name
  before acting on it and compare it with the names in its orders. Until now
  the driving side did pixel checks only.
- The move is incremental: the screen module comes over unchanged and is
  covered by recorded-frame tests first; then capture, accept, and resign;
  create and start come last, since they need inviting by name. The existing
  playbooks keep working until each action is replaced.
- The numbering skips 002: the code cites an "ADR 002" on ingest logging that
  was never committed.
