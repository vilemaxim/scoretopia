# ADR 001: Core Architecture — Bot, Storage, and Platform Boundaries

**Status:** Accepted; superseded in part on 2026-11-23 (see below)  
**Date:** 2026-07-05

## Superseded parts (2026-11-23)

The layering, SQLite, the Discord gateway connection, and the separation of
Discord identity from Polytopia name still stand. Three parts of this decision
were removed from the code and no longer apply. The original text is kept
below, unedited, with each affected section marked.

| Superseded | What replaced it on `main` |
|------------|----------------------------|
| **Input channel.** A second Discord channel where players upload screenshots, read through `on_message`. | **Capture ingest.** Games arrive as capture directories written by a capture playbook on the Steam test machine: `<captures.path>/setup/<key>/` and `<captures.path>/result/<key>/`, each with a `manifest.json`. The bot polls `captures.path` every `captures.poll_seconds` (`domain/captures.py`, `screenshot/captures.py`). There is one channel, `reports`; the bot reads no messages, and a config that still carries `channels.input` is rejected. |
| **Confirmation flow.** Prompting the uploader to confirm or pick a game, prompting the other player to confirm a win ratio, and the `pending_interactions` table, including pending reconciliation of a game-end that arrives before its game-start. | **No confirmation step.** A setup capture creates the game and posts "Game started". A result capture records the winner and end points and posts "Game finished"; when no game matches it, the game is created from the replay's setup panel first, with no start post. A capture that cannot be read records nothing and is retried on the next poll. Each directory is recorded once in `capture_manifests`, and a game id read from the Game Info screen keeps a game captured twice from being posted twice. |
| **Disputes and screenshot-sourced win ratios.** `friend_profile` screenshots as the primary source of pair ratios, confirmed ratios overriding computed ones, and the `disputes` table. | **Computed ratios only.** `player_pair_ratios` is updated when a game completes, with source `computed` (`GameService.complete_game`). No ingest path reads a `friend_profile` screenshot, although its parser remains in `screenshot/`. Schema version 4 drops `pending_interactions` and `disputes`. |

Smaller points that follow from the same change:

- **Publisher.** The domain returns report DTOs; a `ReportPublisher`
  (`reports/publisher.py`) delivers them. `DiscordReportPublisher` sends embeds
  to the reports channel and `StdoutReportPublisher` prints them, which is how
  `scoretopia captures run` and `scoretopia report run` work without Discord.
- **Slash commands.** `/register` and `/report` are the only Discord input.
- **Auto-link.** There is no uploader any more, so the auto-link path under
  "Player identity" has no Discord user to bind in capture ingest; `/register`
  is the working path.
- **`domain/ingest.py` and `domain/win_ratios.py`** in the layer diagram do not
  exist. The domain modules are `captures.py`, `games.py`, `players.py`,
  `matching.py`, and `results.py`.
- **`docs/tasks/`** and the task numbers under "Consequences" refer to a task
  queue that is no longer in the repository.

## Context

> Superseded in part: the two-channel requirement, screenshot-sourced ratios,
> and disputes. See "Superseded parts" above.

Scoretopia tracks Polytopia game results for a friend group via Discord.
Screenshot OCR extraction is implemented (`game_end`, `friend_profile`; `game_basics`
in progress). The next phases add persistence, game lifecycle orchestration,
win-ratio tracking, reports, and a Discord bot adapter.

Requirements:

- Platform-agnostic core: screenshot processing, storage, and reports must not
  depend on Discord.
- Single Discord guild, two channels: input (screenshots + interactions) and
  reports (announcements).
- Win ratios are stored in the database; screenshot-sourced ratios override
  computed stats after the other player confirms.
- Game-end screenshots lack a game name; match by player set against active games.
- Disputes post in the input channel; a separate admin workflow is deferred.

## Decision

### Layered architecture

> Still stands, except the module names under `domain/` and the dispute and
> pick-a-game action DTOs. See "Superseded parts" above.

```
config/scoretopia.yaml
        │
┌───────▼──────────────────────────────────────────┐
│  scoretopia/                                     │
│  ├── screenshot/     OCR + parsers (exists)      │
│  ├── storage/        SQLite repositories         │
│  ├── domain/         business logic              │
│  │   ├── ingest.py                               │
│  │   ├── games.py                                │
│  │   ├── win_ratios.py                           │
│  │   └── players.py                              │
│  ├── reports/        queries + DTO formatters    │
│  └── ports/          BotPort protocol            │
└──────────────────────────────────────────────────┘
        ▲
        │ implements BotPort
  discord/adapter.py   (discord.py, gateway mode)
```

The orchestration layer returns **action DTOs** (e.g. post report, ask user to
pick a game, flag dispute). The Discord adapter renders them. A CLI can call the
same domain functions for testing without Discord.

### Persistence: SQLite

Single file database (`data/scoretopia.db`). Appropriate for a small group,
queryable, no external server. Repositories hide SQL from domain logic.

### Discord connectivity: gateway (long-running process)

`discord.py` opens an **outbound WebSocket** to Discord's gateway. No inbound
ports, public URL, or webhook infrastructure required. The bot process runs
continuously on the host (laptop initially; VPS later if desired).

> Superseded: the bot has no `on_message` handler; it reads no messages.

Polling channel history is **not** required for firewall reasons and is avoided
in favour of `on_message` event handlers.

### Player identity

> Superseded in part: the auto-link path depended on an uploader.

Two linking paths:

1. **Auto-link:** when a user uploads a `game_basics` screenshot and a player
   row has `is_you=True`, bind that Polytopia name to the uploader's Discord ID
   (if not already linked to someone else).
2. **Manual:** `/register <polytopia_name>` slash command.

Discord display name and Polytopia name are stored separately; never assumed equal.

### Win ratio authority

> Superseded: ratios are computed from completed games only.

- Primary updates come from **confirmed win-ratio screenshots** (`friend_profile`).
- Game-end completions also update pair stats.
- When a screenshot is confirmed by the other player, it **wins** over computed
  values (handles pre-bot history and unreported games).
- On rejection, flag as `disputed` and post in the **input channel**; admin
  resolution workflow is deferred (see `docs/tasks/TODO.md`).

### Game lifecycle

> Superseded: games now start and end from captures, with no prompts and no
> pending reconciliation. See "Superseded parts" above.

| Event | Trigger | Action |
|-------|---------|--------|
| Game start | `game_basics` screenshot | Create active game; post to reports channel; reconcile pending game-ends |
| Game end | `game_end` screenshot | Match players to active games; prompt uploader to confirm/pick; complete on confirm |
| Win ratio | `friend_profile` screenshot | Prompt other player to confirm; update or dispute |
| Unrecognized | OCR type detection fails | Reply in input channel with guidance |

**Pending reconciliation:** if a game-end arrives before its game-start, store
as pending. When a matching game-start is ingested, auto-attempt to complete the
pending game-end.

### Reports

Driven by `config/scoretopia.yaml`: report type, schedule (cron), enabled flag,
target channel. Report modules return platform-agnostic DTOs; the adapter renders
embeds. On-demand reports via slash commands regardless of schedule.

### Data model (summary)

> Superseded in part: `pending_interactions` and `disputes` were dropped;
> `capture_manifests` was added.

- **players** — polytopia_name, discord_user_id, discord_display_name
- **games** — name, status (active/completed), settings, timestamps, winner
- **game_participants** — game_id, player_id, tribe, scores, placement
- **pending_interactions** — awaiting user confirm/pick (game end, win ratio)
- **player_pair_ratios** — directional wins between player pairs, source, updated_at
- **disputes** — flagged disagreements with both claimed values

## Consequences

- Most tasks (004–012) are testable without Discord or network access.
- Only task 013 introduces `discord.py` as a dependency.
- Moving the bot off a laptop is "run the same process elsewhere", not an
  architecture change.
- Gateway mode requires the process to stay running; acceptable for v1.

## Alternatives considered

| Alternative | Rejected because |
|-------------|------------------|
| JSON file storage | No query support; poor concurrency |
| Polling channel history | Unnecessary given gateway works behind firewall; more fragile |
| Third review channel | User prefers disputes in input channel; admin flow TBD |
| Webhook/interactions HTTP server | Requires public URL; not needed with gateway bot |
