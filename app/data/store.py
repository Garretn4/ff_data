"""SQLite app state: raw HTTP cache and market value snapshots."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Mapping

import polars as pl

SNAPSHOT_COLUMNS = (
    "sleeper_id", "name", "position", "team",
    "value", "overall_rank", "position_rank", "trend30",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    source TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    sleeper_id TEXT,
    name TEXT,
    position TEXT,
    team TEXT,
    value REAL,
    overall_rank INTEGER,
    position_rank INTEGER,
    trend30 REAL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_source_time ON market_snapshots (source, taken_at);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # AppState shares this connection across NiceGUI worker threads (each `run.io_bound`
        # call runs on its own thread). This build's sqlite3 module reports threadsafety 3
        # (serialized): the underlying SQLite library was compiled to allow the same
        # connection to be used from multiple threads without external locking, so
        # concurrent calls here are safe.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)

    def get_cached(self, key: str, max_age_s: float | None, now: float) -> str | None:
        row = self._conn.execute(
            "SELECT fetched_at, body FROM http_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        fetched_at, body = row
        if max_age_s is not None and now - fetched_at > max_age_s:
            return None
        return str(body)

    def fetched_at(self, key: str) -> float | None:
        row = self._conn.execute("SELECT fetched_at FROM http_cache WHERE key = ?", (key,)).fetchone()
        return None if row is None else float(row[0])

    def put_cached(self, key: str, body: str, now: float) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO http_cache (key, fetched_at, body) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET fetched_at = excluded.fetched_at, body = excluded.body",
                (key, now, body),
            )

    def put_snapshot(self, source: str, taken_at: str, rows: Iterable[Mapping[str, object]]) -> int:
        values = [
            (source, taken_at, *[row.get(col) for col in SNAPSHOT_COLUMNS]) for row in rows
        ]
        placeholders = ", ".join("?" for _ in range(2 + len(SNAPSHOT_COLUMNS)))
        with self._conn:
            self._conn.executemany(
                f"INSERT INTO market_snapshots (source, taken_at, {', '.join(SNAPSHOT_COLUMNS)}) "
                f"VALUES ({placeholders})",
                values,
            )
        return len(values)

    def latest_snapshot(self, source: str) -> pl.DataFrame:
        cols = ["source", "taken_at", *SNAPSHOT_COLUMNS]
        rows = self._conn.execute(
            f"SELECT {', '.join(cols)} FROM market_snapshots WHERE source = ? "
            "AND taken_at = (SELECT MAX(taken_at) FROM market_snapshots WHERE source = ?)",
            (source, source),
        ).fetchall()
        schema = {
            "source": pl.Utf8, "taken_at": pl.Utf8, "sleeper_id": pl.Utf8, "name": pl.Utf8,
            "position": pl.Utf8, "team": pl.Utf8, "value": pl.Float64,
            "overall_rank": pl.Int64, "position_rank": pl.Int64, "trend30": pl.Float64,
        }
        return pl.DataFrame(rows, schema=schema, orient="row")
