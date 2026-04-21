"""Idempotent SQLite schema initializer for the batch analyzer.

Creates (or opens) ./data/analyzer.db with WAL journal mode and the 7 tables
specified in handoff/BATCH_DESIGN.md §A.1. Safe to run multiple times — every
CREATE uses IF NOT EXISTS.

Usage:
    venv/bin/python scripts/init_db.py
    venv/bin/python scripts/init_db.py --db /custom/path.db

Also importable:
    from scripts.init_db import init_db, get_connection
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "analyzer.db"

SCHEMA_SQL = """
-- 1) properties
CREATE TABLE IF NOT EXISTS properties (
    url_hash                    TEXT PRIMARY KEY,
    canonical_url               TEXT NOT NULL,
    address                     TEXT,
    zip_code                    TEXT,
    first_seen_at               TEXT NOT NULL,
    last_scraped_at             TEXT NOT NULL,
    scrape_count                INTEGER NOT NULL DEFAULT 1,
    last_price                  INTEGER,
    last_dom                    INTEGER,
    llm_analysis                TEXT,
    llm_analyzed_at             TEXT,
    llm_model                   TEXT,
    llm_input_tokens            INTEGER,
    llm_cached_input_tokens     INTEGER,
    llm_output_tokens           INTEGER,
    cached_insurance            INTEGER,
    cached_insurance_breakdown  TEXT,
    cache_stale_reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_properties_zip ON properties(zip_code);
CREATE INDEX IF NOT EXISTS idx_properties_last_scraped ON properties(last_scraped_at);
CREATE INDEX IF NOT EXISTS idx_properties_llm_analyzed_at ON properties(llm_analyzed_at);

-- 2) scrape_snapshots
CREATE TABLE IF NOT EXISTS scrape_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash       TEXT NOT NULL,
    scraped_at     TEXT NOT NULL,
    price          INTEGER,
    beds           INTEGER,
    baths          REAL,
    sqft           INTEGER,
    year_built     INTEGER,
    units          INTEGER,
    dom            INTEGER,
    description    TEXT,
    image_url      TEXT,
    raw_json       TEXT,
    scrape_ok      INTEGER NOT NULL DEFAULT 1,
    error_reason   TEXT,
    FOREIGN KEY (url_hash) REFERENCES properties(url_hash) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_urlhash_time ON scrape_snapshots(url_hash, scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_zip_time ON scrape_snapshots(url_hash, scraped_at);

-- 3) batches
-- `scan_request_id` (Sprint 17 Bundle 2): nullable group key for per-ZIP
-- sub-batches that belong to a single Scan ZIPs submission. Lets
-- /api/scan-status/{request_id} aggregate progress across N ZIPs with
-- one poll instead of N. Single-URL + paste-batch submissions leave it
-- null (no grouping needed).
CREATE TABLE IF NOT EXISTS batches (
    batch_id          TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    completed_at      TEXT,
    mode              TEXT NOT NULL CHECK (mode IN ('sync','async')),
    input_count       INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('pending','running','complete','failed','partial')),
    external_batch_id TEXT,
    preset_name       TEXT,
    error_reason      TEXT,
    scan_request_id   TEXT
    -- scraped_count is added via _ADDITIVE_MIGRATIONS below; keeping
    -- the migration path working for existing DBs means not
    -- declaring it inline here.
);
CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);
CREATE INDEX IF NOT EXISTS idx_batches_scan_req ON batches(scan_request_id) WHERE scan_request_id IS NOT NULL;

-- 3a) batch_url_hashes — tracks which URLs were submitted in each batch so
-- overlapping concurrent batches don't cross-contaminate each other's
-- reconstructed input lists at poll time. One row per URL (including
-- skipped/cache-hit rows) with its position in the original submission.
CREATE TABLE IF NOT EXISTS batch_url_hashes (
    batch_id  TEXT NOT NULL,
    url_hash  TEXT NOT NULL,
    position  INTEGER NOT NULL,
    PRIMARY KEY (batch_id, url_hash),
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE,
    FOREIGN KEY (url_hash) REFERENCES properties(url_hash) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_batch_url_hashes_batch ON batch_url_hashes(batch_id);

-- 4) rankings
CREATE TABLE IF NOT EXISTS rankings (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id             TEXT NOT NULL,
    url_hash             TEXT NOT NULL,
    rank                 INTEGER NOT NULL,
    topsis_score         REAL NOT NULL,
    pareto_efficient     INTEGER NOT NULL DEFAULT 0,
    verdict              TEXT NOT NULL,
    hard_fail            INTEGER NOT NULL DEFAULT 0,
    reasons_json         TEXT NOT NULL,
    criteria_json        TEXT NOT NULL,
    derived_metrics_json TEXT NOT NULL,
    claude_narrative     TEXT,
    narrative_status     TEXT NOT NULL DEFAULT 'pending'
                         CHECK (narrative_status IN ('pending','ok','failed','skipped')),
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE,
    FOREIGN KEY (url_hash) REFERENCES properties(url_hash) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rankings_batch_url ON rankings(batch_id, url_hash);
CREATE INDEX IF NOT EXISTS idx_rankings_batch_rank ON rankings(batch_id, rank);

-- 5) claude_runs
CREATE TABLE IF NOT EXISTS claude_runs (
    run_id              TEXT PRIMARY KEY,
    batch_id            TEXT NOT NULL,
    url_hash            TEXT,
    mode                TEXT NOT NULL CHECK (mode IN ('sync','async')),
    external_batch_id   TEXT,
    prompt_cache_hit    INTEGER,
    input_tokens        INTEGER,
    cached_input_tokens INTEGER,
    output_tokens       INTEGER,
    cost_usd            REAL,
    created_at          TEXT NOT NULL,
    completed_at        TEXT,
    status              TEXT NOT NULL CHECK (status IN ('pending','ok','failed')),
    error_reason        TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_claude_runs_batch ON claude_runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_claude_runs_created ON claude_runs(created_at DESC);

-- 6) property_enrichment
CREATE TABLE IF NOT EXISTS property_enrichment (
    url_hash           TEXT PRIMARY KEY,
    lat                REAL,
    lng                REAL,
    geocode_source     TEXT,
    flood_zone         TEXT,
    flood_zone_risk    TEXT,
    fire_zone          TEXT,
    fire_zone_risk     TEXT,
    amenity_counts     TEXT,
    walkability_index  INTEGER,
    enriched_at        TEXT NOT NULL,
    fetch_errors_json  TEXT,
    FOREIGN KEY (url_hash) REFERENCES properties(url_hash) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_enrichment_flood ON property_enrichment(flood_zone_risk);
CREATE INDEX IF NOT EXISTS idx_enrichment_fire ON property_enrichment(fire_zone_risk);

-- 7) rent_comps_cache
CREATE TABLE IF NOT EXISTS rent_comps_cache (
    zip_code       TEXT NOT NULL,
    beds           INTEGER NOT NULL,
    baths          REAL NOT NULL,
    payload_json   TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (zip_code, beds, baths)
);
CREATE INDEX IF NOT EXISTS idx_rent_comps_fetched ON rent_comps_cache(fetched_at);

-- 8) overpass_cache (Sprint 8-2) — cache OSM Overpass walkability fetches
-- keyed by a lat/lng bucket (~100m cell). Two URLs in the same cell share
-- one fetch; TTL 30 days since walkability changes slowly.
CREATE TABLE IF NOT EXISTS overpass_cache (
    lat_bucket    REAL NOT NULL,
    lng_bucket    REAL NOT NULL,
    payload_json  TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (lat_bucket, lng_bucket)
);
CREATE INDEX IF NOT EXISTS idx_overpass_fetched ON overpass_cache(fetched_at);
"""


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply per-connection PRAGMAs per BATCH_DESIGN §A.1."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


TABLE_NAMES = (
    "properties",
    "scrape_snapshots",
    "property_enrichment",
    "rent_comps_cache",
    "overpass_cache",
    "batches",
    "batch_url_hashes",
    "rankings",
    "claude_runs",
)


# Additive ALTER migrations. Wrapped in try/except because SQLite errors on
# duplicate-column ALTER, which is our idempotency signal.
_ADDITIVE_MIGRATIONS = (
    ("batches", "scraped_count", "INTEGER"),
    # Sprint 17 Bundle 2: scan_request_id groups per-ZIP sub-batches
    # under one scan submission so /api/scan-status/{request_id} can
    # roll up N batches into one poll.
    ("batches", "scan_request_id", "TEXT"),
)

# Sprint 17 Bundle 2: explicit index creations run post-migration. The
# CREATE INDEX IF NOT EXISTS statements in SCHEMA_SQL cover fresh DBs
# but any index added ALONGSIDE a migrated column needs its own pass.
_POST_MIGRATION_INDEXES = (
    (
        "idx_batches_scan_req",
        "CREATE INDEX IF NOT EXISTS idx_batches_scan_req "
        "ON batches(scan_request_id) WHERE scan_request_id IS NOT NULL",
    ),
)


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    for table, column, coltype in _ADDITIVE_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError:
            # Column already exists — idempotent no-op.
            pass
    for _name, sql in _POST_MIGRATION_INDEXES:
        try:
            conn.execute(sql)
        except sqlite3.Error:
            # Review P1 on PR #48: broadened from OperationalError to
            # Error for symmetry with other handlers in this file and
            # defense against DatabaseError on corrupt-DB edge cases.
            # Index already exists / referenced column missing / DB
            # malformed — all safe no-ops; the app keeps running.
            pass


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Create (or upgrade) the schema. Returns the resolved DB path."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _apply_pragmas(conn)
        conn.executescript(SCHEMA_SQL)
        _apply_additive_migrations(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


def summarize(db_path: Path | str = DEFAULT_DB_PATH) -> list[tuple[str, int]]:
    """Return [(table_name, row_count)] for every expected table."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        rows = []
        for name in TABLE_NAMES:
            cur = conn.execute(f"SELECT COUNT(*) FROM {name}")
            rows.append((name, cur.fetchone()[0]))
        return rows
    finally:
        conn.close()


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with the required pragmas set. Callers must close."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the analyzer SQLite DB.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite DB file")
    args = parser.parse_args()
    path = init_db(args.db)
    print(f"Initialized schema at {path}")
    print()
    print(f"{'table_name':<22} | row_count")
    print(f"{'-' * 22}-+-{'-' * 9}")
    for name, count in summarize(path):
        print(f"{name:<22} | {count:>9}")


if __name__ == "__main__":
    main()
