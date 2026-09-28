"""Time-series metric store on SQLite with tiered downsampling and retention.

Series are named `<device>|<metric>|<subject>` (e.g. `leaf1|if.rx_bps|Ethernet49`) and
selected with shell-style globs. Every write lands in the raw tier and is folded into
1-minute and 1-hour aggregates in the same transaction; retention follows the
blueprint (7 days raw, 90 days 1-minute, 365 days 1-hour). A query picks the finest
tier that still covers its window. ClickHouse or VictoriaMetrics can replace this
behind `write_many` / `query` / `latest`.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

DAY = 86400.0
RETENTION = {"raw": 7 * DAY, "m1": 90 * DAY, "h1": 365 * DAY}
BUCKET = {"m1": 60, "h1": 3600}


@dataclass(frozen=True)
class Point:
    ts: float
    value: float
    min: float | None = None
    max: float | None = None


class MetricStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS raw (series TEXT NOT NULL, ts REAL NOT NULL, value REAL NOT NULL,
                                            PRIMARY KEY (series, ts));
            CREATE TABLE IF NOT EXISTS m1 (series TEXT NOT NULL, bucket INTEGER NOT NULL, n INTEGER NOT NULL,
                                           total REAL NOT NULL, lo REAL NOT NULL, hi REAL NOT NULL,
                                           PRIMARY KEY (series, bucket));
            CREATE TABLE IF NOT EXISTS h1 (series TEXT NOT NULL, bucket INTEGER NOT NULL, n INTEGER NOT NULL,
                                           total REAL NOT NULL, lo REAL NOT NULL, hi REAL NOT NULL,
                                           PRIMARY KEY (series, bucket));
            """)
        self.now = 0.0

    def write_many(self, points: list[tuple[str, float, float]]) -> None:
        if not points:
            return
        with self._lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO raw (series, ts, value) VALUES (?, ?, ?)", points)
            for tier, size in BUCKET.items():
                self._db.executemany(
                    f"""INSERT INTO {tier} (series, bucket, n, total, lo, hi) VALUES (?, ?, 1, ?, ?, ?)
                        ON CONFLICT (series, bucket) DO UPDATE SET
                          n = n + 1, total = total + excluded.total,
                          lo = MIN(lo, excluded.lo), hi = MAX(hi, excluded.hi)""",
                    [(s, int(ts // size) * size, v, v, v) for s, ts, v in points])
            self.now = max(self.now, max(ts for _, ts, _ in points))

    def write(self, series: str, ts: float, value: float) -> None:
        self.write_many([(series, ts, value)])

    def prune(self, now: float | None = None) -> dict[str, int]:
        now = self.now if now is None else now
        removed: dict[str, int] = {}
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM raw WHERE ts < ?", (now - RETENTION["raw"],))
            removed["raw"] = cur.rowcount
            for tier in BUCKET:
                cur = self._db.execute(f"DELETE FROM {tier} WHERE bucket < ?", (now - RETENTION[tier],))
                removed[tier] = cur.rowcount
        return removed

    def series(self, pattern: str = "*") -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT series FROM raw WHERE series GLOB ? ORDER BY series",
                                    (pattern,)).fetchall()
        return [r[0] for r in rows]

    def tier_for(self, start: float, step: float) -> str:
        age = self.now - start
        if age <= RETENTION["raw"] and step < BUCKET["m1"]:
            return "raw"
        if age <= RETENTION["m1"] and step < BUCKET["h1"]:
            return "m1"
        return "h1"

    def query(self, pattern: str, start: float, end: float, step: float = 0.0) -> dict[str, list[Point]]:
        """Points per matching series in [start, end]; `step` selects the aggregation tier."""
        tier = self.tier_for(start, step)
        with self._lock:
            if tier == "raw":
                rows = self._db.execute(
                    "SELECT series, ts, value, NULL, NULL FROM raw WHERE series GLOB ? AND ts BETWEEN ? AND ? "
                    "ORDER BY series, ts", (pattern, start, end)).fetchall()
            else:
                rows = self._db.execute(
                    f"SELECT series, bucket, total / n, lo, hi FROM {tier} WHERE series GLOB ? "
                    f"AND bucket BETWEEN ? AND ? ORDER BY series, bucket",
                    (pattern, int(start // BUCKET[tier]) * BUCKET[tier], end)).fetchall()
        out: dict[str, list[Point]] = {}
        for series, ts, value, lo, hi in rows:
            out.setdefault(series, []).append(Point(float(ts), float(value), lo, hi))
        return out

    def latest(self, pattern: str) -> dict[str, Point]:
        with self._lock:
            rows = self._db.execute(
                "SELECT r.series, r.ts, r.value FROM raw r JOIN (SELECT series, MAX(ts) AS ts FROM raw "
                "WHERE series GLOB ? GROUP BY series) m ON r.series = m.series AND r.ts = m.ts "
                "ORDER BY r.series", (pattern,)).fetchall()
        return {s: Point(float(ts), float(v)) for s, ts, v in rows}

    def close(self) -> None:
        with self._lock:
            self._db.close()
