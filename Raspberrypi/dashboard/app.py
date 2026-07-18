#!/usr/bin/env python3
"""Local web dashboard for the ESP32 Polar H10 data.

Serves a live-updating page that reads straight from the SAME hr_data.db the
receiver writes to (read-only — it never writes or locks it). Open it from any
device on the same network as the Pi:

    http://pi4server.local:8000

Config via environment variables:
    HR_DB   path to hr_data.db  (default: ../hr_receiver/hr_data.db next to this)
    PORT    port to serve on    (default: 8000)

Run manually:
    .venv/bin/python3 app.py
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request

# The receiver's DB sits in a sibling folder by default (see deploy-dashboard.sh).
DEFAULT_DB = Path(__file__).resolve().parent.parent / "hr_receiver" / "hr_data.db"
DB_PATH = Path(os.environ.get("HR_DB", DEFAULT_DB))
PORT = int(os.environ.get("PORT", "8000"))

# Feed counts as "live" if a reading arrived within this many seconds.
LIVE_WINDOW_S = 20

app = Flask(__name__)


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


def _cutoff(minutes):
    return (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def series(table, cols, minutes, target=2500):
    """Return up to ~`target` rows from `table`, downsampled evenly over the range.

    Downsampling keeps the accelerometer plot light on a 1GB Pi even for long
    sessions: we take every Nth row so the browser never draws 100k points.
    """
    where, args = [], []
    if minutes:
        where.append("received >= ?")
        args.append(_cutoff(minutes))
    wc = ("WHERE " + " AND ".join(where)) if where else ""

    total = q(f"SELECT COUNT(*) AS n FROM {table} {wc}", args)
    n = total[0]["n"] if total else 0
    step = max(1, n // target)

    cond = where + [f"id % {step} = 0"] if step > 1 else where
    wc2 = ("WHERE " + " AND ".join(cond)) if cond else ""
    rows = q(f"SELECT {cols} FROM {table} {wc2} ORDER BY id ASC LIMIT {target * 2}", args)
    return rows, n


def _seconds_since(received):
    if not received:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(received)).total_seconds()
    except ValueError:
        return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    hr_last = q("SELECT received, bpm FROM readings ORDER BY id DESC LIMIT 1")
    acc_last = q("SELECT received FROM acc ORDER BY id DESC LIMIT 1")
    hr_count = q("SELECT COUNT(*) AS n FROM readings")
    acc_count = q("SELECT COUNT(*) AS n FROM acc")

    since = _seconds_since(hr_last[0]["received"] if hr_last else None)
    return jsonify({
        "db_found": DB_PATH.exists(),
        "live": since is not None and since <= LIVE_WINDOW_S,
        "seconds_since_last": None if since is None else round(since, 1),
        "last_received": hr_last[0]["received"] if hr_last else None,
        "current_bpm": hr_last[0]["bpm"] if hr_last else None,
        "hr_count": hr_count[0]["n"] if hr_count else 0,
        "acc_count": acc_count[0]["n"] if acc_count else 0,
    })


@app.route("/api/data")
def data():
    minutes = request.args.get("minutes", type=int)   # None = all time

    # Keep point counts small so the browser (often a phone) stays smooth and the
    # 1GB Pi isn't re-drawing tens of thousands of points on every refresh.
    hr, hr_n = series("readings", "received, t_ms, bpm, rr_ms", minutes, target=1000)
    acc, acc_n = series("acc", "received, t_ms, x, y, z", minutes, target=700)

    bpms = [r["bpm"] for r in hr]
    stats = {}
    if bpms:
        stats = {
            "min": min(bpms),
            "max": max(bpms),
            "avg": round(sum(bpms) / len(bpms), 1),
            "duration_s": _span_seconds(hr),
        }

    return jsonify({
        "hr": hr,
        "acc": acc,
        "hr_total": hr_n,
        "acc_total": acc_n,
        "stats": stats,
    })


def _span_seconds(rows):
    if len(rows) < 2:
        return 0
    try:
        t0 = datetime.fromisoformat(rows[0]["received"])
        t1 = datetime.fromisoformat(rows[-1]["received"])
        return int((t1 - t0).total_seconds())
    except (ValueError, KeyError):
        return 0


if __name__ == "__main__":
    print(f"Reading database: {DB_PATH}  (exists: {DB_PATH.exists()})")
    print(f"Dashboard on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
