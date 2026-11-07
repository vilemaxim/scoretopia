# Scoretopia

Discord bot for tracking **Polytopia** game results in your friend group's server.
The bot reads each game's setup and result screens from captures taken on a Steam
test machine, records the game, posts when it starts and when it ends, and posts
standings on request.

This guide covers setup on **Discord** (application, bot, server channels) and on
**your computer** (Python environment, configuration, running the bot).

For TDD agent orchestration setup, see [SETUP.md](SETUP.md).

---

## Overview

Scoretopia expects:

- **One Discord server** with one text channel:
  - **Reports** — game started and game finished posts, and report output
- **A captures directory** the bot can read: one directory per game per kind,
  each with a `manifest.json`, as the steam-test capture playbook writes them
  (see [Captures](#captures) below).
- **A machine that stays online** while you want the bot active (laptop or VPS).
  The bot connects *outbound* to Discord; you do not need to open inbound ports or
  run a webhook server.

---

## Part 1 — Discord setup

### 1. Create a Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**, name it (e.g. `Scoretopia`), and create it.

### 2. Create the bot user

1. Open your application → **Bot** in the left sidebar.
2. Click **Add Bot** (or **Reset Token** if you already have one).
3. Copy the **bot token** and store it somewhere safe. You will set it as an
   environment variable on your computer — **never commit it to git**.

### 3. Gateway intents

Scoretopia needs no privileged gateway intents: it reads no messages.
`Message Content Intent` and `Server Members Intent` can stay off.

### 4. Generate an invite URL

1. Go to **OAuth2** → **URL Generator**.
2. Under **Scopes**, select:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, select at least:
   - View Channels
   - Send Messages
   - Embed Links
4. Copy the generated URL, open it in a browser, choose your server, and
   authorize the bot.

The bot only supports **one guild** today; invite it to the server where your
group plays.

### 5. Create the reports channel

Create one text channel (the name is configurable; this matches the example
config):

| Role    | Example name         | Purpose                                              |
|---------|----------------------|------------------------------------------------------|
| Reports | `#polytopia-reports` | Game started and game finished posts, `/report` output |

Make sure the bot role can **view and send messages** in it.

### 6. Register players (after the bot is running)

Each player links their Discord account to their in-game Polytopia name:

```
/register polytopia_name:YourInGameName
```

---

## Part 2 — Computer setup

### Prerequisites

- **Python 3.12+**
- **git**
- Enough disk space for EasyOCR model files (downloaded automatically on the
  first capture read; cached under `.easyocr_models/` in the project root)

### 1. Clone and install

```bash
git clone https://github.com/vilemaxim/scoretopia scoretopia
cd scoretopia

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e .
```

Verify the CLI is available:

```bash
scoretopia --help
```

### 2. Configuration file

Copy the example config and edit the channel name and captures path:

```bash
cp config/scoretopia.example.yaml config/scoretopia.yaml
```

Edit `config/scoretopia.yaml`:

```yaml
channels:
  reports: polytopia-reports      # must match your reports channel name exactly

database:
  path: data/scoretopia.db

captures:
  path: data/captures             # where capture directories arrive
  poll_seconds: 300               # how often the bot looks for new ones
```

The channel value is a **channel name**, not an ID. The bot resolves it when it
starts and fails fast if the channel is missing. A config that still carries
`channels.input` is rejected: the screenshot channel was removed.

`config/scoretopia.yaml` is local configuration — do not commit secrets or
server-specific paths if you customize them.

### 3. Bot token (environment variable)

Export the token from the Developer Portal (step 2 above):

```bash
export SCORETOPIA_DISCORD_TOKEN='your-bot-token-here'
```

On Linux/macOS you can add that line to `~/.bashrc` or use a `.env` file loaded
by your shell or process manager. **Do not** put the token in `scoretopia.yaml`
or any committed file.

### 4. Data directories

The bot creates `data/scoretopia.db` (SQLite) automatically on first run. Ensure
the user running the bot can write under `config/`'s parent directory (or
whatever paths you configured), and can read `captures.path`.

### Captures

The steam-test capture playbook (homelab-playbooks
`playbooks/steam-polytopia-capture.yml`) writes one directory per game per kind:

```
<captures.path>/setup/<key>/    card.png, info.png, manifest.json
<captures.path>/result/<key>/   setup.png, info.png, victory.png, manifest.json
```

`manifest.json` carries `kind`, `captured_at`, `screens`, and `start`. The bot
processes each directory once and records it in the database:

- **setup** — reads the game card, creates the game, and posts **Game started**
  (players, map size and type, Glory or Might, points needed, created or
  invited — `unknown` until the capture can tell).
- **result** — reads the replay's setup panel and victory screen, records the
  winner and end points against the game, and posts **Game finished**. A result
  for a game whose setup was never captured creates the game first, with no
  start post.

A game id read from `info.png` keeps a game captured twice from being posted
twice; without one, each directory counts once. A capture that cannot be read
posts nothing and is retried on the next poll. A directory with no
`manifest.json` yet is still being written and is skipped until it has one.

---

## Part 3 — Run the bot

From the project root with the virtual environment activated and the token set:

```bash
scoretopia bot
```

Optional: point at a different config file:

```bash
scoretopia bot --config /path/to/scoretopia.yaml
```

You should see a log line like `Discord bot ready; slash commands synced`. In
Discord, slash commands `/register` and `/report` should appear within a minute
(global command sync can take longer on first install).

The process must **keep running** while you want the bot online. Use `tmux`,
`screen`, or a systemd service on a VPS for a persistent setup.

The bot polls `captures.path` every `captures.poll_seconds` and posts each new
game start and end to the reports channel.

To process the captures directory once without Discord, printing each post to
the terminal instead (for testing):

```bash
scoretopia captures run
scoretopia captures run --config /path/to/scoretopia.yaml
```

This records what it processes in the same database the bot uses, so the bot
will not post those games again. It ends by printing two lines to stderr:

```
Captures processed: N posted
Captures remaining: M
```

`M` is the complete capture directories still not recorded after the run.

To bound a run, pass `--limit N`: it stops after N unprocessed directories, in
the order the bot reads them (setups first, then by capture time), whatever
each one's outcome — a capture that cannot be read counts toward N and is
retried on the next run. Run it again to continue with the next N:

```bash
scoretopia captures run --limit 20
```

The bot's own poll has no limit.

To start from the captures already on disk without reading or posting them —
on a first deploy, say — mark them all processed:

```bash
scoretopia captures seed
scoretopia captures seed --config /path/to/scoretopia.yaml
```

`seed` records every complete, unprocessed directory under `captures.path` as
`seeded`, with no game, loads no OCR, and posts nothing. It prints
`Captures seeded: N` to stderr. A directory with no `manifest.json` yet is left
for the bot, and directories already processed keep their record. After a seed,
`captures run` and the bot skip every seeded directory.

The database migrates itself on open: a database from before `seed` existed
(schema version 2) has its `capture_manifests` table rebuilt, rows kept, so it
accepts `seeded`.

### Smoke test

1. Run `scoretopia captures run` against a captures directory holding a setup
   capture, and check it prints a "Game started" post.
2. Start `scoretopia bot` and check the reports channel for posts from captures
   that arrive after that.
3. Run `/register` if a player is not linked yet.
4. Run `/report active_games` to post a report to the reports channel.

### Scheduled reports (optional)

`scoretopia.yaml` defines cron schedules for automatic reports. The long-running
`scoretopia bot` process handles Discord interactions; scheduled report delivery
to Discord is configured in the same file but requires the report scheduler to
be running with a Discord-backed publisher. For on-demand use, `/report` is
enough.

To print reports to the terminal instead:

```bash
scoretopia report run --name active_games
scoretopia report run --all
```

---

## Development

```bash
bash scripts/lint.sh
bash scripts/test.sh
```

Screenshot OCR CLI (no Discord):

```bash
# Human-readable text (default)
scoretopia-extract path/to/screenshot.png

# JSON matching ExtractionResult shape (author goldens from the same pipeline)
scoretopia-extract path/to/screenshot.png --format json
scoretopia-extract path/to/screenshot.png --format json -o path/to/expected.json

# Verify OCR output against an expected JSON file (exit 0 on match)
scoretopia-extract path/to/screenshot.png --expected path/to/expected.json
```

The multiplayer menu cards are the fallback for the in-game Game Basics modal,
and the steam-test captures read them; they produce the same `game_basics`
extraction:

- **Multiplayer → Ongoing** — active game card (RESIGN, Game Timer, waiting text)
- **Multiplayer → Replays** — finished replay card (Share instead of timer)

On finished replay cards, `game_timer` and `turn_status` may be null; player names
are still required for registration.

A card's **More info** button opens the Game Info modal, which produces a
`game_info` extraction: the game id (a UUID, wrapped over two lines on screen),
game version, and turn. A lobby's modal shows no version or turn, so both are
null. Capture ingest takes its game id from this extraction of `info.png`.

Local golden pairs under `samples/screenshots/`: place a same-stem `.json` next
to a `.png` / `.jpg` / `.jpeg`. Pytest discovers those pairs and compares
**every field** of the live OCR extraction with the golden (via
`compare_extraction_to_expected`). Player names are first resolved against the
names in all local goldens, as production resolves them against the players
table: a name that differs from exactly one known player only by dots or by
`1`/`l`/`I` takes that player's spelling. Images without a matching `.json` are
skipped.

Per-field accuracy across the goldens, as counts only (no field values, so no
player names are printed):

```bash
scoretopia-ocr-accuracy                  # samples/screenshots, names resolved
scoretopia-ocr-accuracy --no-roster      # raw OCR names
scoretopia-ocr-accuracy path/to/samples
```

Steam captures (landscape frames, 1280x720 from steam-test) go through
`scoretopia.screenshot.steam`: it re-reads the small panel text from magnified
crops and parses relative to the panel's labels. Phone screenshots use the
original parsers unchanged.

Production code under `src/scoretopia/` must **not** hardcode human player
names from your local sample JSON; a pytest guard enforces this so parsers stay
generic. Use `--expected-names-only` with `scoretopia-extract` for the same
name-only check from the CLI:

```bash
scoretopia-extract path/to/screenshot.png --expected path/to/expected.json --expected-names-only
```

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `Missing Discord bot token` | `SCORETOPIA_DISCORD_TOKEN` not set in the shell running `scoretopia bot` |
| `Discord channel not found: …` | Channel name in `scoretopia.yaml` does not match the server exactly |
| `channels.input was removed` | Delete `channels.input` from `scoretopia.yaml`; games now come from captures |
| Slash commands missing | Wait a few minutes after first start; re-invite with `applications.commands` scope |
| No game posts | Nothing new under `captures.path`, or the directories have no `manifest.json` yet — run `scoretopia captures run` and read its log |
| `capture extraction failed … retry next poll` in the log | OCR could not read that capture; it is retried each poll |
| First capture is slow | EasyOCR is downloading models into `.easyocr_models/` |

---

## Architecture

See [docs/adr/001-core-architecture.md](docs/adr/001-core-architecture.md) for
layering, storage, and Discord gateway design.
