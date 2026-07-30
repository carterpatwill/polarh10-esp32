"""Session list, detail traces, rename and delete."""
import sqlite3

from flask import Blueprint, jsonify, request

from .config import ACC_SAMPLE_RATE_HZ, ACC_TARGET, HR_TARGET
from .db import bucket_of, dur_seconds, one, q, table_columns, write

bp = Blueprint("sessions", __name__)


@bp.route("/api/sessions")
def sessions():
    """Every session that actually recorded data, newest first."""
    # `kind` ('train'/'metric') only exists on DBs from the newer server. Fall
    # back to NULL so the dashboard still works against an older database.
    kind_sel = "s.kind" if "kind" in table_columns("sessions") else "NULL"
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
        dur = dur_seconds(r["started"], r["ended"])
        if dur is None:
            last = one("SELECT MAX(received) AS m FROM hr WHERE session = ?", (r["id"],))
            dur = dur_seconds(r["started"], last["m"] if last else None)
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


@bp.route("/api/session/<int:sid>")
def session_detail(sid):
    kind_sel = "kind" if "kind" in table_columns("sessions") else "NULL AS kind"
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

    dur = dur_seconds(meta["started"], meta["ended"])
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


@bp.route("/api/session/<int:sid>/name", methods=["POST"])
def rename_session(sid):
    """Set (or clear) a session's custom name. Empty name reverts to the
    date/time default shown by the UI."""
    if not one("SELECT id FROM sessions WHERE id = ?", (sid,)):
        return jsonify({"error": "no such session"}), 404
    name = (request.get_json(silent=True) or {}).get("name", "")
    name = name.strip() or None
    try:
        write("UPDATE sessions SET label = ? WHERE id = ?", (name, sid))
    except sqlite3.OperationalError as e:
        return jsonify({"error": f"could not save: {e}"}), 500
    return jsonify({"ok": True, "label": name})


@bp.route("/api/session/<int:sid>", methods=["DELETE"])
def delete_session(sid):
    """Permanently remove a session and all of its HR/ACC readings. Used to clear
    out the near-empty bounce artifacts and duplicate uploads (see the s0001…
    phantom sessions from the filename-labeled firmware)."""
    if not one("SELECT id FROM sessions WHERE id = ?", (sid,)):
        return jsonify({"error": "no such session"}), 404
    try:
        write("DELETE FROM hr WHERE session = ?", (sid,))
        write("DELETE FROM acc WHERE session = ?", (sid,))
        write("DELETE FROM sessions WHERE id = ?", (sid,))
    except sqlite3.OperationalError as e:
        return jsonify({"error": f"could not delete: {e}"}), 500
    return jsonify({"ok": True})
