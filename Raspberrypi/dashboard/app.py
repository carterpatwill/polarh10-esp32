#!/usr/bin/env python3
"""Web dashboard for the ESP32 Polar H10 data — session browser.

Reads straight from the SAME hr_data.db the receiver writes to (read-only — it
never writes or locks it). Shows a list of every recorded session; click one to
see the full heart-rate and accelerometer traces for that workout.

Open it from any device on the same network as the Pi:

    http://pi4server.local:8000

Config via environment variables:
    HR_DB   path to hr_data.db  (default: ../server/hr_data.db next to this)
    PORT    port to serve on    (default: 8000)

Run manually:
    .venv/bin/python3 app.py
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template

# The receiver's DB sits in a sibling folder by default (see deploy-dashboard.sh).
DEFAULT_DB = Path(__file__).resolve().parent.parent / "server" / "hr_data.db"
DB_PATH = Path(os.environ.get("HR_DB", DEFAULT_DB))
PORT = int(os.environ.get("PORT", "8000"))

# Cap points sent to the browser so long sessions stay smooth on a phone / 1GB Pi.
ACC_TARGET = 4000
HR_TARGET = 3000

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


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def _dur_seconds(started, ended):
    """Wall-clock seconds between two ISO timestamps, or None if unknown."""
    if not started or not ended:
        return None
    try:
        return int((datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds())
    except ValueError:
        return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/sessions")
def sessions():
    """Every session that actually recorded data, newest first."""
    rows = q("""
        SELECT s.id, s.started, s.ended, s.label,
               (SELECT COUNT(*) FROM hr r WHERE r.session = s.id) AS hr_count,
               (SELECT COUNT(*) FROM acc a WHERE a.session = s.id)      AS acc_count
        FROM sessions s
        ORDER BY s.started DESC
    """)
    out = []
    for r in rows:
        if not (r["hr_count"] or r["acc_count"]):
            continue                       # skip empty label-test sessions
        # Prefer the recorded end time; fall back to the last reading we have.
        dur = _dur_seconds(r["started"], r["ended"])
        if dur is None:
            last = one("SELECT MAX(received) AS m FROM hr WHERE session = ?", (r["id"],))
            dur = _dur_seconds(r["started"], last["m"] if last else None)
        out.append({
            "id": r["id"],
            "started": r["started"],
            "ended": r["ended"],
            "label": r["label"],
            "hr_count": r["hr_count"],
            "acc_count": r["acc_count"],
            "duration_s": dur,
        })
    return jsonify(out)


def _series(table, cols, sid, t0, target):
    """Full session rows from `table`, evenly downsampled to ~`target` points.

    x is elapsed seconds from the session's first sample (`t0`) so HR and acc
    share one timeline even though each table has its own t_ms baseline.
    """
    n = one(f"SELECT COUNT(*) AS n FROM {table} WHERE session = ?", (sid,))
    total = n["n"] if n else 0
    step = max(1, total // target)
    cond = f"WHERE session = {int(sid)}"
    if step > 1:
        cond += f" AND id % {step} = 0"
    rows = q(f"SELECT {cols} FROM {table} {cond} ORDER BY id ASC")
    for r in rows:
        r["t"] = round((r["t_ms"] - t0) / 1000.0, 2)   # elapsed seconds
    return rows, total


@app.route("/api/session/<int:sid>")
def session_detail(sid):
    meta = one("SELECT id, started, ended, label FROM sessions WHERE id = ?", (sid,))
    if meta is None:
        return jsonify({"error": "no such session"}), 404

    # Common t0 across both tables so the two charts line up on elapsed time.
    t0row = one("""
        SELECT MIN(m) AS t0 FROM (
            SELECT MIN(t_ms) AS m FROM hr WHERE session = ?
            UNION ALL
            SELECT MIN(t_ms) AS m FROM acc WHERE session = ?
        )
    """, (sid, sid))
    t0 = (t0row["t0"] if t0row and t0row["t0"] is not None else 0)

    hr, hr_n = _series("hr", "id, t_ms, bpm", sid, t0, HR_TARGET)
    acc, acc_n = _series("acc", "id, t_ms, x, y, z", sid, t0, ACC_TARGET)

    bpms = [r["bpm"] for r in hr]
    stats = {}
    if bpms:
        stats = {"min": min(bpms), "max": max(bpms), "avg": round(sum(bpms) / len(bpms))}

    dur = _dur_seconds(meta["started"], meta["ended"])
    if dur is None and (hr or acc):
        last_t = max([r["t"] for r in hr] + [r["t"] for r in acc])
        dur = int(last_t)

    return jsonify({
        "id": meta["id"],
        "started": meta["started"],
        "ended": meta["ended"],
        "label": meta["label"],
        "duration_s": dur,
        "hr": [{"t": r["t"], "bpm": r["bpm"]} for r in hr],
        "acc": [{"t": r["t"], "x": r["x"], "y": r["y"], "z": r["z"]} for r in acc],
        "hr_total": hr_n,
        "acc_total": acc_n,
        "stats": stats,
    })


if __name__ == "__main__":
    print(f"Reading database: {DB_PATH}  (exists: {DB_PATH.exists()})")
    print(f"Dashboard on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
