"""Thin SQLite helpers over the receiver's hr_data.db.

Everything here is read-only except _write(), which is used only to rename/delete
sessions. The receiver tolerates the brief concurrent access. Queries degrade to
[] / None on a fresh DB where a table doesn't exist yet, so the dashboard boots
against an empty database without erroring.
"""
import sqlite3
from datetime import datetime

from .config import DB_PATH

# Which activity bucket a training label belongs to (mirrors data/activity.py).
# A label joins a bucket if it contains any of that bucket's keywords. "sprint" is
# folded into "run" to match the trained model (see data/activity.py).
BUCKETS = ["walk", "jog", "run", "other"]
BUCKET_KEYWORDS = {
    "walk":   ["walk"],
    "jog":    ["jog"],
    "run":    ["run", "sprint"],
    "other":  ["other", "misc", "idle", "sit", "stand", "still", "rest", "random"],
}


def bucket_of(label):
    """'Slow walk 30' → 'walk', 'sitting' → 'other', unknown → None."""
    if not label:
        return None
    low = label.lower()
    for b in BUCKETS:
        if any(kw in low for kw in BUCKET_KEYWORDS[b]):
            return b
    return None


def q(sql, args=()):
    """Run a read-only query. Returns [] if the DB/table isn't there yet."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []          # table doesn't exist yet (fresh DB)
    finally:
        conn.close()


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def write(sql, args=()):
    """Run a write against the same DB (used only to rename/delete sessions).
    Opens read-write briefly; the receiver tolerates the concurrent access."""
    with sqlite3.connect(DB_PATH, timeout=5) as conn:
        conn.execute(sql, args)
        conn.commit()


def table_columns(table):
    """Column names of a table, or empty set if the DB/table isn't there yet."""
    return {r["name"] for r in q(f"PRAGMA table_info({table})")}


def dur_seconds(started, ended):
    """Wall-clock seconds between two ISO timestamps, or None if unknown."""
    if not started or not ended:
        return None
    try:
        return int((datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds())
    except ValueError:
        return None
