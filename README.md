# Scoretopia

Discord bot for tracking **Polytopia** game results in your friend group's server.
Players upload in-game screenshots; the bot reads them, records games and win
ratios, and posts standings on request.

This guide covers setup on **Discord** (application, bot, server channels) and on
**your computer** (Python environment, configuration, running the bot).

For TDD agent orchestration setup, see [SETUP.md](SETUP.md).

---

## Overview

Scoretopia expects:

- **One Discord server** with two text channels:
  - **Input** — screenshot uploads and confirmation buttons
  - **Reports** — game announcements and leaderboard output
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

### 3. Enable required gateway intents

Still on the **Bot** page, under **Privileged Gateway Intents**, turn on:

- **Message Content Intent** — required so the bot can see image attachments in
  the input channel.

`Server Members Intent` is not required for Scoretopia.

### 4. Generate an invite URL

1. Go to **OAuth2** → **URL Generator**.
2. Under **Scopes**, select:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, select at least:
   - View Channels
   - Send Messages
   - Embed Links
   - Attach Files
   - Read Message History
4. Copy the generated URL, open it in a browser, choose your server, and
   authorize the bot.

The bot only supports **one guild** today; invite it to the server where your
group plays.

### 5. Create channels on your server

Create two text channels (names are configurable; these match the example config):

| Role    | Example name           | Purpose                                      |
|---------|------------------------|----------------------------------------------|
| Input   | `#polytopia-screenshots` | Upload screenshots; confirm/reject buttons |
| Reports | `#polytopia-reports`     | New games and `/report` output             |

Make sure the bot role can **view and send messages** in both channels.

### 6. Register players (after the bot is running)

Each player links their Discord account to their in-game Polytopia name:

```
/register polytopia_name:YourInGameName
```

Alternatively, uploading a **game basics** screenshot can auto-link a player when
their Polytopia profile is marked as "you" in the game.

---

## Part 2 — Computer setup

### Prerequisites

- **Python 3.12+**
- **git**
- Enough disk space for EasyOCR model files (downloaded automatically on first
  screenshot; cached under `.easyocr_models/` in the project root)

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

Copy the example config and edit channel names to match your Discord server:

```bash
cp config/scoretopia.example.yaml config/scoretopia.yaml
```

Edit `config/scoretopia.yaml`:

```yaml
channels:
  input: polytopia-screenshots    # must match your input channel name exactly
  reports: polytopia-reports      # must match your reports channel name exactly

database:
  path: data/scoretopia.db

inbox:
  path: data/inbox
```

Channel values are **channel names**, not IDs. The bot resolves them when it
starts and fails fast if either channel is missing.

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

The bot creates `data/scoretopia.db` (SQLite) and `data/inbox/` (saved
screenshots) automatically on first run. Ensure the user running the bot can
write under `config/`'s parent directory (or whatever paths you configured).

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

### Smoke test

1. In the input channel, upload a Polytopia **game basics** screenshot.
2. Check the reports channel for a "game started" announcement.
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
scoretopia-extract path/to/screenshot.png
```

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `Missing Discord bot token` | `SCORETOPIA_DISCORD_TOKEN` not set in the shell running `scoretopia bot` |
| `Discord channel not found: …` | Channel name in `scoretopia.yaml` does not match the server exactly |
| Bot online but ignores uploads | **Message Content Intent** not enabled in the Developer Portal |
| Slash commands missing | Wait a few minutes after first start; re-invite with `applications.commands` scope |
| `Could not recognize this screenshot` | Wrong screenshot type, or OCR could not read the image — use game basics, game end, or friend profile shots |
| First screenshot is slow | EasyOCR is downloading models into `.easyocr_models/` |

---

## Architecture

See [docs/adr/001-core-architecture.md](docs/adr/001-core-architecture.md) for
layering, storage, and Discord gateway design.
