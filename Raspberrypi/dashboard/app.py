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

# Trained activity guesser (data/activity.py). On the Pi the model is shipped
# next to app.py by deploy-dashboard.sh; locally it falls back to the repo copy.
# ACTIVITY_MODEL overrides both.
_SHIPPED_MODEL = Path(__file__).resolve().parent / "activity_model.joblib"
_REPO_MODEL = (Path(__file__).resolve().parent.parent.parent
               / "data" / "labeled_data" / "activity_model.joblib")
DEFAULT_MODEL = _SHIPPED_MODEL if _SHIPPED_MODEL.exists() else _REPO_MODEL
MODEL_PATH = Path(os.environ.get("ACTIVITY_MODEL", DEFAULT_MODEL))

# Cap points sent to the browser so long sessions stay smooth on a phone / 1GB Pi.
ACC_TARGET = 4000
HR_TARGET = 3000

# The H10 stamps a whole BATCH of accelerometer samples with one t_ms, so per-sample
# time from t_ms is useless (many samples share an x → vertical stripes on the chart).
# ACC streams at a known fixed rate, so we place samples evenly by index instead.
ACC_SAMPLE_RATE_HZ = 25          # must match ACC_SAMPLE_RATE in esp32/src/config.h

app = Flask(__name__)
# Re-read templates from disk on each request so edits show up without a restart.
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Which activity bucket a training label belongs to (mirrors data/activity.py).
# A label joins a bucket if it contains any of that bucket's keywords.
BUCKETS = ["walk", "jog", "run", "sprint", "other"]
BUCKET_KEYWORDS = {
    "walk":   ["walk"],
    "jog":    ["jog"],
    "run":    ["run"],
    "sprint": ["sprint"],
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


def _table_columns(table):
    """Column names of a table, or empty set if the DB/table isn't there yet."""
    return {r["name"] for r in q(f"PRAGMA table_info({table})")}


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
    # `kind` ('train'/'metric') only exists on DBs from the newer server. Fall
    # back to NULL so the dashboard still works against an older database.
    kind_sel = "s.kind" if "kind" in _table_columns("sessions") else "NULL"
    rows = q(f"""
        SELECT s.id, s.started, s.ended, s.label, {kind_sel} AS kind,
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
            "kind": r["kind"],
            "bucket": bucket_of(r["label"]),
            "hr_count": r["hr_count"],
            "acc_count": r["acc_count"],
            "duration_s": dur,
        })
    return jsonify(out)


def _series(table, cols, sid, t0, target, fixed_rate=None):
    """Full session rows from `table`, evenly downsampled to ~`target` points.

    Each row gets an elapsed-seconds `t` for the x-axis. Two ways to set it:
      * default — from t_ms relative to `t0` (fine for HR, which has real per-
        reading timestamps).
      * fixed_rate=<Hz> — space samples evenly by their order at a known sample
        rate. Used for ACC, whose batched t_ms would otherwise stack many samples
        on one x and flatten the waveform into vertical stripes.
    """
    n = one(f"SELECT COUNT(*) AS n FROM {table} WHERE session = ?", (sid,))
    total = n["n"] if n else 0
    step = max(1, total // target)
    cond = f"WHERE session = {int(sid)}"
    if step > 1:
        cond += f" AND id % {step} = 0"
    rows = q(f"SELECT {cols} FROM {table} {cond} ORDER BY id ASC")
    if fixed_rate:
        dt = step / fixed_rate           # seconds between kept samples
        for i, r in enumerate(rows):
            r["t"] = round(i * dt, 3)
    else:
        for r in rows:
            r["t"] = round((r["t_ms"] - t0) / 1000.0, 2)   # elapsed seconds
    return rows, total


@app.route("/api/session/<int:sid>")
def session_detail(sid):
    kind_sel = "kind" if "kind" in _table_columns("sessions") else "NULL AS kind"
    meta = one(f"SELECT id, started, ended, label, {kind_sel} FROM sessions WHERE id = ?", (sid,))
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
    acc, acc_n = _series("acc", "id, t_ms, x, y, z", sid, t0, ACC_TARGET,
                         fixed_rate=ACC_SAMPLE_RATE_HZ)

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
        "kind": meta["kind"],
        "bucket": bucket_of(meta["label"]),
        "duration_s": dur,
        "hr": [{"t": r["t"], "bpm": r["bpm"]} for r in hr],
        "acc": [{"t": r["t"], "x": r["x"], "y": r["y"], "z": r["z"]} for r in acc],
        "hr_total": hr_n,
        "acc_total": acc_n,
        "stats": stats,
    })


def _load_acc_samples(sid):
    """All ACC samples for a session as an (N,3) float array, arrival order."""
    import numpy as np
    rows = q("SELECT x, y, z FROM acc WHERE session = ? ORDER BY id", (sid,))
    if not rows:
        return np.empty((0, 3))
    return np.array([[r["x"], r["y"], r["z"]] for r in rows], dtype=float)


# Cache the model between requests, reloading only if the file changed on disk.
_model_cache = {"mtime": None, "bundle": None}


def _get_model():
    if not MODEL_PATH.exists():
        return None
    import joblib
    mtime = MODEL_PATH.stat().st_mtime
    if _model_cache["mtime"] != mtime:
        _model_cache["bundle"] = joblib.load(MODEL_PATH)
        _model_cache["mtime"] = mtime
    return _model_cache["bundle"]


@app.route("/api/session/<int:sid>/timeline")
def session_timeline(sid):
    """Model-guessed activity segments (walk/jog/run/sprint/still) over the session."""
    try:
        from activity_timeline import analyze
        bundle = _get_model()
    except Exception as e:                       # numpy/joblib/sklearn missing
        return jsonify({"error": f"activity timeline unavailable: {e}"}), 501
    if bundle is None:
        return jsonify({"error": f"no trained model at {MODEL_PATH}"}), 404

    samples = _load_acc_samples(sid)
    if len(samples) == 0:
        return jsonify({"error": "no accelerometer data for this session"}), 404
    return jsonify(analyze(samples, bundle))


if __name__ == "__main__":
    print(f"Reading database: {DB_PATH}  (exists: {DB_PATH.exists()})")
    print(f"Activity model:   {MODEL_PATH}  (exists: {MODEL_PATH.exists()})")
    print(f"Dashboard on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
