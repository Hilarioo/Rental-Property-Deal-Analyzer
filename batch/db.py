"""SQLite helpers for the batch pipeline.

- `url_hash` normalization matches the Scrappy pattern documented in
  BATCH_DESIGN.md §A.2: lowercased scheme+host, trailing slash stripped,
  query params sorted, fragment dropped.
- `with_immediate_tx` implements the retry-on-lock wrapper for
  `BEGIN IMMEDIATE` writes (H.3: 100/300/900 ms).
- `get_connection` is re-exported from scripts.init_db so there is one
  canonical pragma set.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from scripts.init_db import DEFAULT_DB_PATH, get_connection, init_db

__all__ = [
    "DEFAULT_DB_PATH",
    "get_connection",
    "init_db",
    "normalize_url",
    "url_hash",
    "utc_now_iso",
    "new_uuid_hex",
    "with_immediate_tx",
]

T = TypeVar("T")


def utc_now_iso() -> str:
    """ISO-8601 UTC with 'Z' suffix (matches existing spec samples)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_uuid_hex() -> str:
    return uuid.uuid4().hex


def normalize_url(url: str) -> str:
    """Lowercase scheme+host, strip trailing slash, sort query, drop fragment."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    # Strip trailing slash (except root).
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query_pairs = sorted(parse_qsl(parts.query, keep_blank_values=True))
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(url: str) -> str:
    """SHA-256 hex of the normalized URL."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def with_immediate_tx(
    conn: sqlite3.Connection,
    fn: Callable[[sqlite3.Connection], T],
    max_attempts: int = 3,
) -> T:
    """Run `fn(conn)` inside BEGIN IMMEDIATE ... COMMIT with exponential retry
    on `database is locked` (§H.3).
    """
    delays = [0.1, 0.3, 0.9]
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn)
                conn.execute("COMMIT")
                return result
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower():
                raise
            if attempt == max_attempts - 1:
                break
            time.sleep(delays[attempt])
    raise sqlite3.OperationalError(
        f"database is locked after {max_attempts} attempts: {last_error}"
    )
