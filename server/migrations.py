"""The database schema as ordered SQL scripts.

Migration 1 is the frozen version 1.0 baseline. A committed migration is never edited:
later schema changes append scripts that advance ``PRAGMA user_version`` by one.
"""

# ``GOLF`` in ASCII. This distinguishes the 1.0 baseline from disposable databases made
# by the pre-release migration chain, including its otherwise-ambiguous user_version 1.
APPLICATION_ID = 0x474F4C46

MIGRATIONS: list[str] = [
    # 1: version 1.0 baseline (docs/randomizer_devplan.md, "Data model")
    f"""
    PRAGMA application_id = {APPLICATION_ID};

    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        discord_id TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL,
        global_name TEXT,
        avatar TEXT,
        player_id INTEGER NOT NULL UNIQUE CHECK (player_id BETWEEN 1 AND 4294967295),
        created_at TEXT NOT NULL,
        last_login TEXT NOT NULL
    );

    CREATE TABLE seeds (
        id TEXT PRIMARY KEY CHECK (length(id) = 10),
        qr_seed_id INTEGER NOT NULL UNIQUE CHECK (qr_seed_id BETWEEN 1 AND 839299365868340223),
        manifest TEXT NOT NULL,
        generator_version INTEGER NOT NULL,
        catalog_version INTEGER NOT NULL,
        curation_stamp TEXT NOT NULL,
        unfinished_ips BLOB NOT NULL,
        creator_id INTEGER,
        created_at TEXT NOT NULL
    );

    CREATE TABLE seed_holes (
        seed_id TEXT NOT NULL REFERENCES seeds (id),
        position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 18),
        hole_id TEXT NOT NULL,
        transforms TEXT NOT NULL,
        par INTEGER NOT NULL,
        wind_seed INTEGER NOT NULL,
        pin_index INTEGER NOT NULL,
        wind_direction INTEGER NOT NULL,
        wind_speed INTEGER NOT NULL,
        PRIMARY KEY (seed_id, position)
    );

    CREATE TABLE entries (
        id INTEGER PRIMARY KEY,
        seed_id TEXT NOT NULL REFERENCES seeds (id),
        user_id INTEGER NOT NULL REFERENCES users (id),
        player_name TEXT NOT NULL,
        clubs TEXT NOT NULL,
        key_slot0 BLOB NOT NULL CHECK (length(key_slot0) = 8),
        key_slot1 BLOB NOT NULL CHECK (length(key_slot1) = 8),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (seed_id, user_id)
    );

    CREATE INDEX entries_by_user ON entries (user_id, created_at);

    CREATE TABLE rounds (
        id INTEGER PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE CHECK (length(public_id) = 10),
        entry_id INTEGER NOT NULL REFERENCES entries (id),
        slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
        payload BLOB NOT NULL CHECK (length(payload) = 36),
        total_strokes INTEGER NOT NULL,
        total_putts INTEGER NOT NULL,
        received_at TEXT NOT NULL,
        flagged INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0, 1)),
        flag_note TEXT,
        UNIQUE (entry_id, slot)
    );

    CREATE TABLE round_holes (
        round_id INTEGER NOT NULL REFERENCES rounds (id),
        position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 18),
        strokes INTEGER NOT NULL,
        putts INTEGER NOT NULL,
        PRIMARY KEY (round_id, position)
    );

    CREATE TABLE voided_rounds (
        id INTEGER PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE CHECK (length(public_id) = 10),
        entry_id INTEGER NOT NULL REFERENCES entries (id),
        slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
        payload BLOB NOT NULL UNIQUE CHECK (length(payload) = 36),
        received_at TEXT NOT NULL,
        flagged INTEGER NOT NULL CHECK (flagged IN (0, 1)),
        flag_note TEXT,
        voided_at TEXT NOT NULL,
        void_note TEXT
    );

    CREATE INDEX voided_rounds_by_entry ON voided_rounds (entry_id, slot);

    CREATE TABLE admin_actions (
        id INTEGER PRIMARY KEY,
        admin_id INTEGER NOT NULL REFERENCES users (id),
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        note TEXT,
        detail TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(detail) AND json_type(detail) = 'object'),
        created_at TEXT NOT NULL
    );

    CREATE INDEX admin_actions_by_target ON admin_actions (target_type, target_id, id);
    CREATE INDEX admin_actions_by_admin ON admin_actions (admin_id, id);
    """,
    # 2: unfinished build provenance and seed withdrawal lifecycle
    """
    ALTER TABLE seeds
        ADD COLUMN build_version INTEGER NOT NULL DEFAULT 1 CHECK (build_version >= 1);

    ALTER TABLE seeds
        ADD COLUMN finish_abi_version INTEGER NOT NULL DEFAULT 1
            CHECK (finish_abi_version >= 1);

    ALTER TABLE seeds
        ADD COLUMN withdrawn_at TEXT;
    """,
    # 3: request timings and their daily rollup
    """
    CREATE TABLE timings (
        id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        request_id TEXT NOT NULL,
        route TEXT NOT NULL,
        method TEXT NOT NULL,
        status INTEGER NOT NULL,
        total_ms REAL NOT NULL,
        outcome TEXT,
        detail TEXT NOT NULL DEFAULT '{}'
            CHECK (json_valid(detail) AND json_type(detail) = 'object')
    );

    CREATE INDEX timings_by_time ON timings (created_at);
    CREATE INDEX timings_by_request_id ON timings (request_id);

    -- Each row's percentiles are computed from the raw samples of the one day it covers,
    -- which is exact for that day. They cannot be re-aggregated: never AVG() a p99 across
    -- rows of this table. A window longer than a day is computed from `timings` while its
    -- raw rows are still inside the retention window.
    CREATE TABLE timing_day (
        day TEXT NOT NULL,
        route TEXT NOT NULL,
        method TEXT NOT NULL,
        count INTEGER NOT NULL,
        errors INTEGER NOT NULL,
        p50_ms REAL NOT NULL,
        p90_ms REAL NOT NULL,
        p99_ms REAL NOT NULL,
        max_ms REAL NOT NULL,
        PRIMARY KEY (day, route, method)
    );
    """,
    # 4: a signed-in player's saved download settings (docs/planning/download_settings.md)
    """
    CREATE TABLE download_settings (
        user_id INTEGER PRIMARY KEY REFERENCES users (id),
        settings TEXT NOT NULL
            CHECK (json_valid(settings) AND json_type(settings) = 'object'),
        updated_at TEXT NOT NULL
    );
    """,
    # 5: scorecard QR protocol version 2: 39-byte payloads carrying fairways hit and penalty
    # strokes (docs/scorecard_qr.md). Both are NULL for a round whose payload is version 1,
    # which did not record them. SQLite cannot change a CHECK in place, so the two tables
    # whose payload length it pins are rebuilt. round_holes refers to rounds, so its rows
    # are set aside, the table dropped and made again under its own name, and the rows put
    # back: with foreign keys deferred, the drop's violations are settled by the reinsert
    # before COMMIT checks them.
    """
    PRAGMA defer_foreign_keys = ON;

    CREATE TEMP TABLE rounds_before_v5 AS SELECT * FROM rounds;
    DROP TABLE rounds;
    CREATE TABLE rounds (
        id INTEGER PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE CHECK (length(public_id) = 10),
        entry_id INTEGER NOT NULL REFERENCES entries (id),
        slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
        payload BLOB NOT NULL CHECK (length(payload) IN (36, 39)),
        total_strokes INTEGER NOT NULL,
        total_putts INTEGER NOT NULL,
        penalty_strokes INTEGER CHECK (penalty_strokes BETWEEN 0 AND 63),
        received_at TEXT NOT NULL,
        flagged INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0, 1)),
        flag_note TEXT,
        UNIQUE (entry_id, slot),
        CHECK ((length(payload) = 36) = (penalty_strokes IS NULL))
    );
    INSERT INTO rounds (id, public_id, entry_id, slot, payload, total_strokes, total_putts,
                        received_at, flagged, flag_note)
        SELECT id, public_id, entry_id, slot, payload, total_strokes, total_putts,
               received_at, flagged, flag_note
        FROM rounds_before_v5;
    DROP TABLE rounds_before_v5;

    ALTER TABLE round_holes
        ADD COLUMN fairway_hit INTEGER CHECK (fairway_hit IN (0, 1));

    CREATE TEMP TABLE voided_rounds_before_v5 AS SELECT * FROM voided_rounds;
    DROP TABLE voided_rounds;
    CREATE TABLE voided_rounds (
        id INTEGER PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE CHECK (length(public_id) = 10),
        entry_id INTEGER NOT NULL REFERENCES entries (id),
        slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
        payload BLOB NOT NULL UNIQUE CHECK (length(payload) IN (36, 39)),
        received_at TEXT NOT NULL,
        flagged INTEGER NOT NULL CHECK (flagged IN (0, 1)),
        flag_note TEXT,
        voided_at TEXT NOT NULL,
        void_note TEXT
    );
    INSERT INTO voided_rounds SELECT * FROM voided_rounds_before_v5;
    DROP TABLE voided_rounds_before_v5;
    CREATE INDEX voided_rounds_by_entry ON voided_rounds (entry_id, slot);
    """,
    # 6: a seed's transformed holes, as built (ADR 0021). `data` is the zlib-compressed
    # canonical JSON its content hash is taken over, and a hole two seeds share is stored
    # once. A slot whose `data_hash` is NULL has no transforms, and its hole is the
    # catalog's.
    """
    CREATE TABLE hole_data (
        content_hash TEXT PRIMARY KEY CHECK (length(content_hash) = 64),
        data BLOB NOT NULL
    );

    ALTER TABLE seed_holes
        ADD COLUMN data_hash TEXT REFERENCES hole_data (content_hash);
    """,
    # 7: what a seed's pages read from the catalog, kept on the seed, and every slot's hole
    # in `hole_data`, not only the transformed ones (ADR 0024). A seed stored before this
    # has these columns NULL until `golf-site-backfill` fills them in from the catalog and
    # the hole store, which a migration cannot read; the site refuses to start until then.
    # `source_rom`, `source_course` and `source_hole` stay NULL for a hole that is not
    # vanilla.
    """
    ALTER TABLE seeds
        ADD COLUMN required_roms TEXT
            CHECK (required_roms IS NULL
                   OR (json_valid(required_roms) AND json_type(required_roms) = 'array'));

    ALTER TABLE seed_holes ADD COLUMN distance INTEGER;
    ALTER TABLE seed_holes ADD COLUMN author TEXT;
    ALTER TABLE seed_holes ADD COLUMN source_rom TEXT;
    ALTER TABLE seed_holes ADD COLUMN source_course TEXT;
    ALTER TABLE seed_holes ADD COLUMN source_hole INTEGER;
    """,
    # 8: hosted ROMs, a seed's ROM a host builds for the players who claim its two slots
    # (docs/planning/hosted_roms.md). `options` is the hosted `SavedSettings` JSON, NULL
    # until the host saves them; `downloaded_at` is set by a download and cleared by a
    # release.
    """
    CREATE TABLE hosted_roms (
        id INTEGER PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE CHECK (length(public_id) = 10),
        seed_id TEXT NOT NULL REFERENCES seeds (id),
        host_id INTEGER NOT NULL REFERENCES users (id),
        token TEXT NOT NULL UNIQUE,
        options TEXT CHECK (options IS NULL OR json_valid(options)),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        downloaded_at TEXT
    );
    CREATE INDEX hosted_roms_by_host ON hosted_roms (host_id);

    CREATE TABLE hosted_rom_slots (
        hosted_rom_id INTEGER NOT NULL REFERENCES hosted_roms (id),
        slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
        user_id INTEGER REFERENCES users (id),
        claimed_at TEXT,
        CHECK ((user_id IS NULL) = (claimed_at IS NULL)),
        PRIMARY KEY (hosted_rom_id, slot)
    );
    """,
    # 9: the daily seed (docs/competitive/data_model.md, ADR 0033). A day's window is
    # stored as it was, so a later change to the rollover leaves past days alone, and so is
    # its theme, so a later change to the schedule leaves past days' names alone. A day's
    # seed is its latest attempt: 2 is the rerun that replaced 1. A card is a player's own
    # round on the day's seed while it was open; it is kept when its round is voided, and
    # counts only while the round is recorded.
    """
    CREATE TABLE dailies (
        day TEXT PRIMARY KEY CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        theme_name TEXT NOT NULL CHECK (theme_name <> ''),
        theme_description TEXT CHECK (theme_description <> ''),
        request TEXT NOT NULL CHECK (json_valid(request) AND json_type(request) = 'object'),
        opens_at TEXT NOT NULL,
        closes_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        withdrawn_at TEXT
    );

    CREATE TABLE daily_seeds (
        day TEXT NOT NULL REFERENCES dailies (day),
        attempt INTEGER NOT NULL CHECK (attempt IN (1, 2)),
        seed_id TEXT NOT NULL UNIQUE REFERENCES seeds (id),
        replaced_at TEXT,
        PRIMARY KEY (day, attempt)
    );

    CREATE TABLE daily_cards (
        day TEXT NOT NULL REFERENCES dailies (day),
        user_id INTEGER NOT NULL REFERENCES users (id),
        round_public_id TEXT NOT NULL UNIQUE CHECK (length(round_public_id) = 10),
        created_at TEXT NOT NULL,
        PRIMARY KEY (day, user_id)
    );
    """,
    # 10: ratings (docs/competitive/data_model.md, Ratings runs). A run is the hole model
    # refitted for one rollover day: its own numbers in `model` and every effect it fitted
    # in ratings_effects, the parts a new seed is rated from. A seed's ratings are written
    # once, under the latest run, and never rewritten.
    """
    CREATE TABLE ratings_runs (
        id INTEGER PRIMARY KEY,
        day TEXT NOT NULL UNIQUE CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        created_at TEXT NOT NULL,
        model_version INTEGER NOT NULL,
        parameters_hash TEXT NOT NULL CHECK (length(parameters_hash) = 64),
        parameters TEXT NOT NULL CHECK (json_valid(parameters)),
        model TEXT NOT NULL CHECK (json_valid(model)),
        latest_received_at TEXT NOT NULL,
        n_rounds INTEGER NOT NULL CHECK (n_rounds > 0)
    );

    CREATE TABLE ratings_effects (
        run_id INTEGER NOT NULL REFERENCES ratings_runs (id),
        grp TEXT NOT NULL CHECK (grp IN ('layout', 'terrain', 'pin')),
        level TEXT NOT NULL,
        value REAL NOT NULL,
        PRIMARY KEY (run_id, grp, level)
    ) WITHOUT ROWID;

    CREATE TABLE seed_ratings (
        seed_id TEXT PRIMARY KEY REFERENCES seeds (id),
        run_id INTEGER NOT NULL REFERENCES ratings_runs (id),
        rated_at TEXT NOT NULL,
        par INTEGER NOT NULL,
        course_rating REAL NOT NULL,
        slope REAL NOT NULL
    );

    CREATE TABLE seed_hole_ratings (
        seed_id TEXT NOT NULL REFERENCES seed_ratings (seed_id),
        position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 18),
        rating REAL NOT NULL,
        spread REAL NOT NULL,
        stroke_index INTEGER NOT NULL CHECK (stroke_index BETWEEN 1 AND 18),
        PRIMARY KEY (seed_id, position)
    ) WITHOUT ROWID;
    """,
    # 11: handicapped dailies (docs/competitive/data_model.md). A day copies the rules it
    # is scored by when it opens; a day opened before this has none until the rollover
    # fills it. A card keeps what its player was shown: the index the round was scored
    # with, the daily handicap, and the player's index and low mark after it, all NULL on
    # a card not yet filled; its holes are the net card.
    """
    ALTER TABLE dailies ADD COLUMN rules TEXT
        CHECK (rules IS NULL OR (json_valid(rules) AND json_type(rules) = 'object'));

    ALTER TABLE daily_cards ADD COLUMN index_before REAL;
    ALTER TABLE daily_cards ADD COLUMN handicap INTEGER;
    ALTER TABLE daily_cards ADD COLUMN index_after REAL;
    ALTER TABLE daily_cards ADD COLUMN low_mark REAL;

    CREATE TABLE daily_card_holes (
        day TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 18),
        received INTEGER NOT NULL,
        net INTEGER NOT NULL,
        points INTEGER NOT NULL CHECK (points >= 0),
        PRIMARY KEY (day, user_id, position),
        FOREIGN KEY (day, user_id) REFERENCES daily_cards (day, user_id)
    ) WITHOUT ROWID;
    """,
    # 12: a daily's results (docs/competitive/data_model.md, Results): rebuilt from its
    # filled cards whose rounds are recorded whenever a card is filled or such a round is
    # voided or restored. A match is stored once, its players in id order; a halved one
    # has no winner.
    """
    CREATE TABLE daily_results (
        day TEXT NOT NULL REFERENCES dailies (day),
        user_id INTEGER NOT NULL REFERENCES users (id),
        points INTEGER NOT NULL CHECK (points >= 0),
        position INTEGER NOT NULL CHECK (position >= 1),
        skins INTEGER NOT NULL CHECK (skins >= 0),
        won INTEGER NOT NULL CHECK (won IN (0, 1)),
        PRIMARY KEY (day, user_id)
    ) WITHOUT ROWID;

    CREATE TABLE daily_matches (
        day TEXT NOT NULL REFERENCES dailies (day),
        user_a INTEGER NOT NULL REFERENCES users (id),
        user_b INTEGER NOT NULL REFERENCES users (id),
        holes_a INTEGER NOT NULL CHECK (holes_a BETWEEN 0 AND 18),
        holes_b INTEGER NOT NULL CHECK (holes_b BETWEEN 0 AND 18),
        winner_id INTEGER REFERENCES users (id),
        margin INTEGER NOT NULL CHECK (margin BETWEEN 0 AND 18),
        CHECK (user_a < user_b),
        CHECK (winner_id IS NULL OR winner_id IN (user_a, user_b)),
        PRIMARY KEY (day, user_a, user_b)
    ) WITHOUT ROWID;
    """,
]
