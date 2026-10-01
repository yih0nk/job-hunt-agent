"""SQLite storage. Documents (profile/preferences/settings) are JSON blobs; jobs are rows."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional, TypeVar

from pydantic import BaseModel

from .models import Preferences, Profile, Settings
from .paths import db_path

T = TypeVar("T", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  key TEXT PRIMARY KEY,
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,               -- stable hash of company|title|location
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT DEFAULT '',
  url TEXT DEFAULT '',
  source TEXT DEFAULT '',
  age_days REAL,
  first_seen REAL NOT NULL,
  description TEXT DEFAULT '',       -- fetched JD text
  resolved_url TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new',
  score INTEGER,                     -- weighted 0-100 total
  score_detail TEXT,                 -- FitScore JSON
  package TEXT,                      -- package JSON (tailored resume + answers)
  notes TEXT DEFAULT '',
  applied_at REAL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started REAL NOT NULL,
  finished REAL,
  summary TEXT
);
"""

# Status lifecycle. "applied" and later are only ever set by the user.
STATUSES = ["new", "scored", "review", "ineligible", "drafted", "applied",
            "interviewing", "offer", "rejected", "archived"]

_lock = threading.RLock()   # one connection shared across worker threads


class Store:
    def __init__(self, path: Optional[str] = None):
        self.path = str(path or db_path())
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        try:
            os.chmod(self.path, 0o600)   # holds the API key + personal details
        except OSError:
            pass

    # --- documents --------------------------------------------------------------
    def get_doc(self, key: str, model: type[T]) -> T:
        rows = self._q("SELECT body FROM docs WHERE key=?", (key,))
        row = rows[0] if rows else None
        return model.model_validate_json(row["body"]) if row else model()

    def put_doc(self, key: str, doc: BaseModel) -> None:
        with _lock:
            self.conn.execute("INSERT INTO docs(key, body) VALUES(?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                              (key, doc.model_dump_json()))
            self.conn.commit()

    def profile(self) -> Profile:
        return self.get_doc("profile", Profile)

    def preferences(self) -> Preferences:
        return self.get_doc("preferences", Preferences)

    def settings(self) -> Settings:
        return self.get_doc("settings", Settings)

    # --- jobs -------------------------------------------------------------------
    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with _lock:
            return self.conn.execute(sql, args).fetchall()

    def has_job(self, job_id: str) -> bool:
        return bool(self._q("SELECT 1 FROM jobs WHERE id=?", (job_id,)))

    def insert_job(self, row: dict) -> None:
        now = time.time()
        with _lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO jobs(id, company, title, location, url, source, age_days, "
                "first_seen, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (row["id"], row["company"], row["title"], row.get("location", ""),
                 row.get("url", ""), row.get("source", ""), row.get("age_days"), now, now))
            self.conn.commit()

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        for k in ("score_detail", "package"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with _lock:
            self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
            self.conn.commit()

    def job(self, job_id: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM jobs WHERE id=?", (job_id,))
        return _row(rows[0]) if rows else None

    def jobs(self, statuses: Optional[list[str]] = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT * FROM jobs", []
        if statuses:
            q += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args = statuses
        q += " ORDER BY COALESCE(score, -1) DESC, first_seen DESC LIMIT ?"
        return [_row(r) for r in self._q(q, (*args, limit))]

    def tracked_pairs(self) -> list[tuple[str, str]]:
        """(company, title) of everything already drafted or applied — used for dedup."""
        rows = self._q("SELECT company, title FROM jobs WHERE status IN "
                       "('drafted','applied','interviewing','offer','rejected')")
        return [(r["company"], r["title"]) for r in rows]

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._q("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}

    # --- runs -------------------------------------------------------------------
    def start_run(self) -> int:
        with _lock:
            cur = self.conn.execute("INSERT INTO runs(started) VALUES(?)", (time.time(),))
            self.conn.commit()
            return cur.lastrowid

    def finish_run(self, run_id: int, summary: dict) -> None:
        with _lock:
            self.conn.execute("UPDATE runs SET finished=?, summary=? WHERE id=?",
                              (time.time(), json.dumps(summary), run_id))
            self.conn.commit()

    def last_run(self) -> Optional[dict]:
        rows = self._q("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        d = dict(rows[0])
        d["summary"] = json.loads(d["summary"]) if d["summary"] else None
        return d


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("score_detail", "package"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d
