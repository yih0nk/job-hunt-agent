"""SQLite storage. Documents (profile/preferences/settings) are JSON blobs; jobs are rows."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional, TypeVar

from pydantic import BaseModel

from . import keystore
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
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  job_id TEXT,                       -- NULL for calls not tied to a role (resume import)
  kind TEXT NOT NULL,                -- parse | score | tailor | answers
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cache_write INTEGER NOT NULL DEFAULT 0,
  cache_read INTEGER NOT NULL DEFAULT 0,
  cost REAL                          -- USD estimate; NULL when the model's price is unknown
);
CREATE INDEX IF NOT EXISTS usage_job ON usage(job_id);
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
            os.chmod(self.path, 0o600)   # personal details (and the API key if no keychain)
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
        s = self.get_doc("settings", Settings)
        key = keystore.get()
        if key:
            s.api_key = key
        elif s.api_key and keystore.put(s.api_key):
            # A key saved before keychain support: move it out of the database.
            self.put_doc("settings", s.model_copy(update={"api_key": ""}))
        return s

    def put_settings(self, s: Settings) -> None:
        stored = keystore.put(s.api_key)
        self.put_doc("settings", s.model_copy(update={"api_key": ""}) if stored else s)

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
        if not rows:
            return None
        d = _row(rows[0])
        d["cost"] = self.job_cost(job_id)
        return d

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

    # --- usage ------------------------------------------------------------------
    def log_usage(self, job_id: Optional[str], kind: str, model: str, u: dict,
                  cost: Optional[float]) -> None:
        with _lock:
            self.conn.execute(
                "INSERT INTO usage(ts, job_id, kind, model, input_tokens, output_tokens, cache_write, "
                "cache_read, cost) VALUES(?,?,?,?,?,?,?,?,?)",
                (time.time(), job_id, kind, model, u.get("input_tokens", 0), u.get("output_tokens", 0),
                 u.get("cache_write", 0), u.get("cache_read", 0), cost))
            self.conn.commit()

    def spend_since(self, ts: float) -> float:
        return self._q("SELECT COALESCE(SUM(cost), 0) c FROM usage WHERE ts >= ?", (ts,))[0]["c"]

    def job_cost(self, job_id: str) -> dict:
        rows = self._q("SELECT kind, COALESCE(SUM(cost), 0) c FROM usage WHERE job_id=? GROUP BY kind", (job_id,))
        by = {r["kind"]: round(r["c"], 4) for r in rows}
        return {"total": round(sum(by.values()), 4), "by_kind": by}

    def usage_summary(self) -> dict:
        now = time.time()
        day = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        rows = self._q("SELECT kind, COUNT(*) n, COALESCE(SUM(cost), 0) c FROM usage "
                       "WHERE ts >= ? GROUP BY kind", (now - 30 * 86400,))
        return {
            "today": round(self.spend_since(day), 4),
            "last_30_days": round(self.spend_since(now - 30 * 86400), 4),
            "all_time": round(self.spend_since(0), 4),
            "by_kind_30d": {r["kind"]: {"calls": r["n"], "cost": round(r["c"], 4)} for r in rows},
        }

    # --- runs -------------------------------------------------------------------
    def start_run(self) -> int:
        with _lock:
            cur = self.conn.execute("INSERT INTO runs(started) VALUES(?)", (time.time(),))
            self.conn.commit()
            return cur.lastrowid

    def finish_run(self, run_id: int, summary: dict) -> None:
        started = self._q("SELECT started FROM runs WHERE id=?", (run_id,))[0]["started"]
        summary = {**summary, "cost": round(self.spend_since(started), 4)}
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
