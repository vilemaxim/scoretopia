CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    polytopia_name TEXT NOT NULL,
    polytopia_name_normalized TEXT NOT NULL UNIQUE,
    discord_user_id TEXT UNIQUE,
    discord_display_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed')),
    map_size INTEGER,
    terrain TEXT,
    game_type TEXT,
    target_score INTEGER,
    game_timer TEXT,
    winner_player_id INTEGER REFERENCES players (id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS game_participants (
    game_id INTEGER NOT NULL REFERENCES games (id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players (id),
    tribe TEXT,
    score INTEGER,
    placement INTEGER,
    is_bot INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, player_id)
);

CREATE TABLE IF NOT EXISTS pending_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    discord_user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'disputed')),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS player_pair_ratios (
    player_a_id INTEGER NOT NULL REFERENCES players (id),
    player_b_id INTEGER NOT NULL REFERENCES players (id),
    wins INTEGER NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (player_a_id, player_b_id)
);

CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_a_id INTEGER NOT NULL REFERENCES players (id),
    player_b_id INTEGER NOT NULL REFERENCES players (id),
    submitter_player_id INTEGER NOT NULL REFERENCES players (id),
    rejector_player_id INTEGER NOT NULL REFERENCES players (id),
    claimed_wins_a INTEGER NOT NULL,
    claimed_wins_b INTEGER NOT NULL,
    screenshot_path TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
