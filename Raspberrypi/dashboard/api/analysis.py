"""Model-driven analysis: activity timeline + per-effort heart-rate recovery.

Both endpoints lean on the trained walk/jog/run model (activity_timeline.py) and
degrade gracefully — if numpy/sklearn/joblib or the model file are missing (e.g.
on the lean Pi) they return a 501/404 the front-end knows to hide.
"""
from flask import Blueprint, jsonify, request

from .config import MODEL_PATH
from .db import one, q

bp = Blueprint("analysis", __name__)


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


@bp.route("/api/session/<int:sid>/timeline")
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


def _hr_series_shared(sid):
    """HR readings and RR beats on a shared-seconds clock (t=0 at the earlier of the
    HR / ACC streams), plus the ACC stream's offset on that clock. Everything the
    recovery math needs to line HR up against the activity timeline."""
    import json
    hr_rows = q("SELECT t_ms, bpm, rr_ms FROM hr WHERE session = ? ORDER BY t_ms", (sid,))
    acc_min_row = one("SELECT MIN(t_ms) AS m FROM acc WHERE session = ?", (sid,))
    if not hr_rows:
        return None
    acc_min = acc_min_row["m"] if acc_min_row else None
    t0 = hr_rows[0]["t_ms"] if acc_min is None else min(hr_rows[0]["t_ms"], acc_min)

    hr = [{"t": (r["t_ms"] - t0) / 1000.0, "bpm": r["bpm"]} for r in hr_rows]
    rr_beats = []
    for r in hr_rows:
        if not r["rr_ms"]:
            continue
        try:
            vals = json.loads(r["rr_ms"])
        except Exception:
            continue
        for v in (vals if isinstance(vals, list) else [vals]):
            rr_beats.append([(r["t_ms"] - t0) / 1000.0, float(v)])
    acc_offset = 0.0 if acc_min is None else (acc_min - t0) / 1000.0
    return hr, rr_beats, acc_offset


@bp.route("/api/session/<int:sid>/recovery")
def session_recovery(sid):
    """Per-effort heart-rate recovery (HRR60/120, τ, RMSSD@60) + chart overlay data.

    Reuses the activity timeline the model already produces, so no re-classifying:
    its jog/run segments mark the efforts whose cooldowns we score."""
    try:
        from activity_timeline import analyze as activity_analyze
        import recovery_timeline
        bundle = _get_model()
    except Exception as e:
        return jsonify({"error": f"recovery unavailable: {e}"}), 501
    if bundle is None:
        return jsonify({"error": f"no trained model at {MODEL_PATH}"}), 404

    samples = _load_acc_samples(sid)
    series = _hr_series_shared(sid)
    if len(samples) == 0 or series is None:
        return jsonify({"error": "session needs both HR and accelerometer data"}), 404
    hr, rr_beats, acc_offset = series

    # Activity segments are ACC-stream-relative; shift them onto the shared HR clock.
    segs = activity_analyze(samples, bundle).get("segments", [])
    for s in segs:
        s["t0"] += acc_offset
        s["t1"] += acc_offset

    resting = request.args.get("resting", type=int) or recovery_timeline.RESTING_HR_BPM
    return jsonify(recovery_timeline.analyze(hr, rr_beats, segs, resting=resting))
