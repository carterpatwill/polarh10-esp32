"""Morning HRV readiness — the read side of the morning_hrv table.

morning_hrv.py (in ../server) is the producer: it captures one resting reading per
morning over BLE and writes a summary row. This blueprint turns those rows into a
rolling baseline + today's readiness status for the dashboard. Pure reader; if the
table doesn't exist yet (fresh DB) every query degrades to [] and the UI shows the
'no readings' empty state.

The baseline follows the standard morning-HRV approach: track lnRMSSD (log RMSSD is
roughly normally distributed, so a mean ± SD band behaves), keep the last N days, and
read where today sits in that band — inside = balanced, below = under-recovered,
above = elevated.
"""
from statistics import mean, stdev

from flask import Blueprint, jsonify

from .db import q

bp = Blueprint("morning", __name__)

BASELINE_WINDOW_DAYS  = 60   # rolling window the "normal range" is built from
BASELINE_MIN_READINGS = 7    # below this the band is shown but flagged "still building"

_FIELDS = ("date", "captured_at", "session", "duration_s", "beats", "artifact_pct",
           "hr_rest", "rmssd", "ln_rmssd", "sdnn", "pnn50", "resp_rate", "quality")


def _baseline(readings):
    """Rolling lnRMSSD baseline + today's placement, from oldest→newest readings.

    Only 'good' readings feed the band (a fidgety low-quality morning shouldn't move
    your normal range), but today's status is still reported for whatever the latest
    reading is so you always see a number."""
    good = [r for r in readings if r["ln_rmssd"] is not None and r["quality"] == "good"]
    if not good:
        return {"ready": False, "n": 0, "need": BASELINE_MIN_READINGS, "status": None}

    window = good[-BASELINE_WINDOW_DAYS:]
    lns = [r["ln_rmssd"] for r in window]
    m = mean(lns)
    sd = stdev(lns) if len(lns) > 1 else 0.0
    lo, hi = m - sd, m + sd

    today = readings[-1]                       # latest reading, good or not
    tln = today["ln_rmssd"]
    status = None
    delta_pct = None
    if tln is not None:
        status = "balanced" if lo <= tln <= hi else ("low" if tln < lo else "elevated")
        # % change of today's RMSSD vs the baseline geometric mean (nicer than lnRMSSD deltas).
        delta_pct = round((pow(2.718281828, tln - m) - 1) * 100.0, 1)

    return {
        "ready": len(window) >= BASELINE_MIN_READINGS,
        "n": len(window), "need": BASELINE_MIN_READINGS,
        "mean": round(m, 3), "sd": round(sd, 3),
        "lo": round(lo, 3), "hi": round(hi, 3),
        "today": round(tln, 3) if tln is not None else None,
        "today_date": today["date"],
        "status": status,
        "delta_pct": delta_pct,
    }


@bp.route("/api/morning")
def morning():
    """All morning readings (oldest→newest, good for charting) + the rolling baseline."""
    rows = q(f"SELECT {', '.join(_FIELDS)} FROM morning_hrv ORDER BY date ASC")
    return jsonify({
        "readings": rows,
        "baseline": _baseline(rows),
        "today": rows[-1] if rows else None,
    })
