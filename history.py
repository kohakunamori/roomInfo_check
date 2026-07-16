"""SQLite history for dorm electricity samples and events."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lock = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HistoryStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        if not self.db_path.is_absolute():
            self.db_path = Path.cwd() / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        with _lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    room_name TEXT,
                    room_id_label TEXT,
                    amount REAL,
                    electricity REAL,
                    source TEXT,
                    ok INTEGER NOT NULL DEFAULT 1,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    type TEXT NOT NULL,
                    amount_from REAL,
                    amount_to REAL,
                    note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
                """
            )

    def add_sample(
        self,
        *,
        room_name=None,
        room_id_label=None,
        amount=None,
        electricity=None,
        source=None,
        ok=True,
        error=None,
        ts=None,
        recharge_delta=1.0,
    ) -> dict:
        ts = ts or _utc_now_iso()
        amount_f = _to_float(amount)
        elec_f = _to_float(electricity)
        with self._conn() as conn:
            prev = conn.execute(
                "SELECT amount FROM samples WHERE ok=1 AND amount IS NOT NULL "
                "ORDER BY ts DESC, id DESC LIMIT 1"
            ).fetchone()
            conn.execute(
                """
                INSERT INTO samples
                  (ts, room_name, room_id_label, amount, electricity, source, ok, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    room_name,
                    room_id_label,
                    amount_f,
                    elec_f,
                    source,
                    1 if ok else 0,
                    error,
                ),
            )
            sample_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            if (
                ok
                and amount_f is not None
                and prev is not None
                and prev["amount"] is not None
                and amount_f - float(prev["amount"]) >= float(recharge_delta)
            ):
                conn.execute(
                    """
                    INSERT INTO events (ts, type, amount_from, amount_to, note)
                    VALUES (?, 'recharge', ?, ?, ?)
                    """,
                    (
                        ts,
                        float(prev["amount"]),
                        amount_f,
                        f"余额上升 {amount_f - float(prev['amount']):.2f} 元",
                    ),
                )
        return {
            "id": sample_id,
            "ts": ts,
            "room_name": room_name,
            "room_id_label": room_id_label,
            "amount": amount_f,
            "electricity": elec_f,
            "source": source,
            "ok": ok,
            "error": error,
        }

    def add_event(self, event_type: str, *, amount_from=None, amount_to=None, note=None, ts=None):
        ts = ts or _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO events (ts, type, amount_from, amount_to, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, event_type, _to_float(amount_from), _to_float(amount_to), note),
            )

    def latest(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM samples WHERE ok=1 ORDER BY ts DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def history(self, *, hours: int | None = 24 * 7, limit: int = 2000) -> list[dict]:
        limit = max(1, min(int(limit), 20000))
        with self._conn() as conn:
            if hours is None or hours <= 0:
                rows = conn.execute(
                    "SELECT * FROM samples WHERE ok=1 ORDER BY ts ASC, id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                since = (
                    datetime.now(timezone.utc) - timedelta(hours=int(hours))
                ).replace(microsecond=0).isoformat()
                rows = conn.execute(
                    """
                    SELECT * FROM samples
                    WHERE ok=1 AND ts >= ?
                    ORDER BY ts ASC, id ASC
                    LIMIT ?
                    """,
                    (since, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def events(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 2000))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, *, retain_days: int = 90) -> int:
        if retain_days <= 0:
            return 0
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=int(retain_days))
        ).replace(microsecond=0).isoformat()
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            deleted = cur.rowcount
            conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        return deleted

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
            ok = conn.execute("SELECT COUNT(*) AS c FROM samples WHERE ok=1").fetchone()["c"]
            latest = conn.execute(
                "SELECT ts FROM samples WHERE ok=1 ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return {
            "samples_total": total,
            "samples_ok": ok,
            "latest_ts": latest["ts"] if latest else None,
            "db_path": str(self.db_path),
        }
