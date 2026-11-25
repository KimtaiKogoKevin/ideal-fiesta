"""SQLite persistence: agent runs, visitors' own enquiries, draft approvals/edits
and rate-limit counters, so nothing is lost on a page reload or server restart.

Plain `sqlite3` from the standard library, no ORM. The file lives at
BSP_DB_PATH (default data/app.db; on Render point it at the persistent disk,
e.g. /var/data/app.db).

Tables
  runs       one row per finished agent run: email_id + the full event list (JSON)
  enquiries  emails typed into "New enquiry", owned by the visitor who wrote them
  drafts     a visitor's status for a run's draft: edited/approved + edited text
  live_runs  timestamp + IP of every live (paid) run, for the rate limits
"""
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("BSP_DB_PATH", str(ROOT / "data" / "app.db")))
SEED_FILE = ROOT / "data" / "seed_runs.json"

_lock = threading.Lock()  # one writer at a time; SQLite handles readers fine

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT NOT NULL,
    events TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'live',   -- 'live' or 'seed'
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_email ON runs(email_id, id);
CREATE TABLE IF NOT EXISTS enquiries (
    id TEXT PRIMARY KEY,
    visitor TEXT NOT NULL,
    email TEXT NOT NULL,                   -- JSON: from, subject, body, received_at
    image BLOB,
    image_type TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS enquiries_visitor ON enquiries(visitor, created_at);
CREATE TABLE IF NOT EXISTS drafts (
    run_id INTEGER NOT NULL,
    visitor TEXT NOT NULL,
    status TEXT NOT NULL,                  -- 'edited' or 'approved'
    body TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (run_id, visitor)
);
CREATE TABLE IF NOT EXISTS live_runs (ip TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS live_runs_ts ON live_runs(ts);
"""


@contextmanager
def _conn():
    """A connection that commits on success, rolls back on error, and always closes."""
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init(sample_ids: set[str]):
    """Create tables, and load pre-recorded runs for any sample email that has none yet.

    The seed means the sample inbox replays instantly (and for free) on a fresh
    deploy, even before anyone has paid for a live run.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        if SEED_FILE.exists():
            have = {r[0] for r in c.execute("SELECT DISTINCT email_id FROM runs")}
            for email_id, events in json.loads(SEED_FILE.read_text()).items():
                if email_id in sample_ids and email_id not in have:
                    c.execute("INSERT INTO runs (email_id, events, source, created_at) VALUES (?, ?, 'seed', ?)",
                              (email_id, json.dumps(events), time.time()))


# --- Runs ---------------------------------------------------------------------

def save_run(email_id: str, events: list[dict]) -> int:
    with _lock, _conn() as c:
        cur = c.execute("INSERT INTO runs (email_id, events, created_at) VALUES (?, ?, ?)",
                        (email_id, json.dumps(events, default=str), time.time()))
        return cur.lastrowid


def latest_run(email_id: str) -> tuple[int, list[dict]] | None:
    with _conn() as c:
        r = c.execute("SELECT id, events FROM runs WHERE email_id = ? ORDER BY id DESC LIMIT 1",
                      (email_id,)).fetchone()
    return (r["id"], json.loads(r["events"])) if r else None


def latest_run_ids() -> dict[str, int]:
    with _conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT email_id, MAX(id) FROM runs GROUP BY email_id")}


# --- Visitors' own enquiries -------------------------------------------------------

def add_enquiry(visitor: str, email: dict, image: tuple[bytes, str] | None) -> str:
    eid = "ENQ-" + secrets.token_hex(4).upper()
    with _lock, _conn() as c:
        c.execute("INSERT INTO enquiries (id, visitor, email, image, image_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (eid, visitor, json.dumps(email), image[0] if image else None,
                   image[1] if image else None, time.time()))
    return eid


def list_enquiries(visitor: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, email, image IS NOT NULL AS has_image FROM enquiries "
                         "WHERE visitor = ? ORDER BY created_at DESC", (visitor,)).fetchall()
    return [json.loads(r["email"]) | {"id": r["id"], "has_image": bool(r["has_image"])} for r in rows]


def get_enquiry(visitor: str, eid: str) -> tuple[dict, tuple[bytes, str] | None] | None:
    """Only the visitor who wrote an enquiry can read it (or its photo)."""
    with _conn() as c:
        r = c.execute("SELECT email, image, image_type FROM enquiries WHERE id = ? AND visitor = ?",
                      (eid, visitor)).fetchone()
    if not r:
        return None
    image = (r["image"], r["image_type"]) if r["image"] is not None else None
    return json.loads(r["email"]) | {"id": eid}, image


# --- Draft status (per visitor, so one reviewer's approvals don't show for the next) ---

def set_draft(visitor: str, run_id: int, status: str, body: str):
    with _lock, _conn() as c:
        c.execute("INSERT INTO drafts (run_id, visitor, status, body, updated_at) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT(run_id, visitor) DO UPDATE SET status = excluded.status, "
                  "body = excluded.body, updated_at = excluded.updated_at",
                  (run_id, visitor, status, body, time.time()))


def get_draft(visitor: str, run_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT status, body FROM drafts WHERE run_id = ? AND visitor = ?",
                      (run_id, visitor)).fetchone()
    return dict(r) if r else None


def draft_statuses(visitor: str) -> dict[int, str]:
    with _conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT run_id, status FROM drafts WHERE visitor = ?", (visitor,))}


def run_exists(run_id: int) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone() is not None


# --- Rate limits (persistent, so a restart doesn't reset the spend cap) --------------

def take_live_run(ip: str, per_ip_per_hour: int, daily_cap: int) -> str | None:
    """Record a live run if within limits. Returns an error message if over a limit."""
    now = time.time()
    day_start = now - (now % 86400)  # midnight UTC
    with _lock, _conn() as c:
        c.execute("DELETE FROM live_runs WHERE ts < ?", (now - 2 * 86400,))
        ip_count = c.execute("SELECT COUNT(*) FROM live_runs WHERE ip = ? AND ts > ?", (ip, now - 3600)).fetchone()[0]
        if ip_count >= per_ip_per_hour:
            return f"Demo limit: {per_ip_per_hour} live runs per hour. Saved sample runs still work."
        day_count = c.execute("SELECT COUNT(*) FROM live_runs WHERE ts >= ?", (day_start,)).fetchone()[0]
        if day_count >= daily_cap:
            return "The demo's daily budget is used up. Saved sample runs still work."
        c.execute("INSERT INTO live_runs (ip, ts) VALUES (?, ?)", (ip, now))
    return None
