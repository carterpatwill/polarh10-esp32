#!/usr/bin/env python3
"""Heart-rate recovery for the dashboard — how fast HR drops after each effort.

The browser version of data/recovery.py. Given a session's HR curve, its beat-to-
beat RR intervals, and the activity segments the model already produced, it finds
each effort's cooldown and scores how quickly the heart settles.

Self-contained on purpose (numpy only): the dashboard ships to the Pi without the
data/ analysis package, exactly like activity_timeline.py. Keep the thresholds and
the metric definitions in sync with data/recovery.py — this is the same math, fed
from the activity segments instead of re-running the classifier.

A recovery EVENT (the whole idea):
  START  the HR peak that lines up with a jog/run → walk/still motion drop.
  WINDOW the cooldown after it — walking AND standing both count.
  END    the moment motion returns to JOG OR ABOVE ("activity again"), a hard time
         cap, or the recording's end.
One event per effort; a session can hold many (intervals). See the recovery doc.
"""
import numpy as np

# ── Baselines (measured — Pi session 36, 2026-07-25) ─────────────────────────────
RESTING_HR_BPM   = 53
RESTING_RMSSD_MS = 59

# ── Effort / window shape (mirrors data/recovery.py) ─────────────────────────────
ACTIVE_BUCKETS   = {"jog", "run", "sprint"}   # "jog or above" = effort
MIN_ACTIVE_SEC   = 6.0
MIN_PEAK_RISE    = 25
PEAK_LOOKBACK    = 20.0
PEAK_LOOKAHEAD   = 15.0
MAX_RECOVERY_SEC = 300.0
MIN_RECOVERY_SEC = 20.0
BASELINE_MARGIN  = 10

# ── HRV / RR cleaning ─────────────────────────────────────────────────────────────
RR_MIN_MS, RR_MAX_MS = 300, 1500
RR_MAX_JUMP          = 0.20
RMSSD_WIN_SEC        = 30.0
RMSSD_MIN_BEATS      = 5

# ── When a τ fit is trustworthy ───────────────────────────────────────────────────
TAU_MIN_DUR   = 45.0
TAU_MIN_DROP  = 6
TAU_MIN_R2    = 0.60
TAU_MAX_SEC   = 600
HRR_MAX_GAP_SEC = 10.0

# ── Draw a recovery even when it isn't a clean exponential ────────────────────────
# A big HR fall right after an effort is unmistakably recovery even if its shape is
# too noisy for a trustworthy τ (mid-workout dips bounce around). When τ is rejected
# but the fall to the recovery low is real, we still draw a straight peak→low GUIDE
# line (dashed on the chart) so the recovery is visible. τ stays None — we don't
# invent a fit, we just stop hiding the drop.
GUIDE_MIN_DROP = 12     # bpm fall from peak to the recovery low
GUIDE_MIN_SEC  = 15     # …sustained over at least this long (ignore a brief spike)

# ── One cooldown can hold SEVERAL recoveries ──────────────────────────────────────
# You go still and HR falls, you move and it climbs, you go still and it falls again —
# that's two recoveries, not one. We split a cooldown wherever HR rebounds by at least
# REBOUND_DELTA bpm (you clearly moved again); smaller wiggles are just noise. Each
# fall gets its own peak, decay line, and metrics.
REBOUND_DELTA = 12


def _effort_blocks(segments):
    """Merge consecutive jog/run/sprint segments into effort spans (t0, t1).

    Segments are contiguous, so a run of active ones is one effort. Only efforts
    lasting >= MIN_ACTIVE_SEC survive (ignore a stray misread window)."""
    blocks, cur = [], None
    for s in segments:
        if s["activity"] in ACTIVE_BUCKETS:
            if cur is None:
                cur = [s["t0"], s["t1"]]
            else:
                cur[1] = s["t1"]
        elif cur is not None:
            blocks.append(tuple(cur)); cur = None
    if cur is not None:
        blocks.append(tuple(cur))
    return [b for b in blocks if b[1] - b[0] >= MIN_ACTIVE_SEC]


def _descents(hr, delta):
    """Split an HR window into successive FALL segments as (i_hi, i_lo) index pairs.

    Walks the curve tracking whether we're falling or climbing. A fall ends (and a new
    one can begin) only once HR rebounds >= delta above its low — that's "you moved
    again", not sensor jitter. A run that keeps falling to the end is one segment."""
    n = len(hr)
    if n < 2:
        return []
    segs = []
    hi_i = lo_i = 0
    hi_v = lo_v = hr[0]
    falling = True                    # a window starts at the effort peak → falling
    for i in range(1, n):
        v = hr[i]
        if falling:
            if v <= lo_v:
                lo_v, lo_i = v, i
            elif v >= lo_v + delta:    # rebounded: close this fall, start climbing
                segs.append((hi_i, lo_i))
                falling = False
                hi_v, hi_i = v, i
        else:
            if v >= hi_v:
                hi_v, hi_i = v, i
            elif v <= hi_v - delta:    # peaked and turning down: a new fall begins
                falling = True
                lo_v, lo_i = v, i
    if falling:                        # close a fall still in progress at the end
        segs.append((hi_i, lo_i))
    return [(h, l) for h, l in segs if l > h]


def _hr_at(hr_t, bpm, t):
    """HR at an arbitrary time, interpolated — None past the ends or across a gap."""
    if t < hr_t[0] or t > hr_t[-1]:
        return None
    if np.min(np.abs(hr_t - t)) > HRR_MAX_GAP_SEC:
        return None
    return float(np.interp(t, hr_t, bpm))


def _fit_tau(t_rel, hr, resting):
    """τ of HR(t)=resting+(peak-resting)·e^(−t/τ), gated on duration, drop and R²."""
    if t_rel[-1] - t_rel[0] < TAU_MIN_DUR or hr[0] - hr.min() < TAU_MIN_DROP:
        return None
    above = hr - resting > 1.0
    if above.sum() < 4:
        return None
    tt, yy = t_rel[above], np.log(hr[above] - resting)
    slope, intercept = np.polyfit(tt, yy, 1)
    if slope >= 0:
        return None
    resid = yy - (slope * tt + intercept)
    ss_tot = np.sum((yy - yy.mean()) ** 2)
    r2 = 1 - np.sum(resid ** 2) / ss_tot if ss_tot > 0 else 0
    tau = -1.0 / slope
    return float(tau) if r2 >= TAU_MIN_R2 and tau <= TAU_MAX_SEC else None


def _clean_rr(beats):
    kept = [(t, v) for t, v in beats if RR_MIN_MS <= v <= RR_MAX_MS]
    if not kept:
        return []
    good = [kept[0]]
    for t, v in kept[1:]:
        if abs(v - good[-1][1]) / good[-1][1] <= RR_MAX_JUMP:
            good.append((t, v))
    return good


def _rmssd(vals):
    if len(vals) < RMSSD_MIN_BEATS:
        return None
    d = np.diff(vals)
    return float(np.sqrt(np.mean(d * d)))


def analyze(hr, rr_beats, segments, resting=RESTING_HR_BPM):
    """Score every recovery event in a session.

        hr        [{t, bpm}]   HR readings on the shared-seconds clock
        rr_beats  [[t, rr_ms]] every beat-to-beat interval, timed, shared clock
        segments  [{t0,t1,activity}]  the activity timeline, ON THE SAME clock
        resting   the decay floor (bpm)

    Returns {resting, resting_rmssd, events, overlay} — overlay carries the pixels-
    agnostic shapes the chart draws (peak points, shaded windows, fitted decay lines).
    """
    if not hr:
        return {"resting": resting, "resting_rmssd": RESTING_RMSSD_MS,
                "events": [], "overlay": {"peaks": [], "windows": [],
                                          "decays": [], "decay_fitted": []}}

    hr_t = np.array([p["t"] for p in hr], dtype=float)
    bpm  = np.array([p["bpm"] for p in hr], dtype=float)
    rr_clean = _clean_rr([(t, v) for t, v in rr_beats])
    blocks = _effort_blocks(segments)

    events, peaks, windows, decays, decay_fitted = [], [], [], [], []
    for k, (b_start, b_end) in enumerate(blocks):
        lo, hi = b_end - PEAK_LOOKBACK, b_end + PEAK_LOOKAHEAD
        near = (hr_t >= lo) & (hr_t <= hi)
        if not near.any():
            continue
        idx = int(np.argmax(np.where(near, bpm, -np.inf)))
        peak_t, peak_bpm = float(hr_t[idx]), float(bpm[idx])
        if peak_bpm < resting + MIN_PEAK_RISE:
            continue

        next_active = blocks[k + 1][0] if k + 1 < len(blocks) else np.inf
        end_t = min(peak_t + MAX_RECOVERY_SEC, next_active, float(hr_t[-1]))
        if end_t - peak_t < MIN_RECOVERY_SEC:
            continue

        inside = (hr_t >= peak_t) & (hr_t <= end_t)
        t_rel, hr_win = hr_t[inside] - peak_t, bpm[inside]

        # A cooldown can hold several falls (still → move → still). Split it wherever
        # HR rebounds and score each fall as its own recovery, from its own local peak.
        for hi_i, lo_i in _descents(hr_win, REBOUND_DELTA):
            s_peak_t, s_peak_bpm = peak_t + float(t_rel[hi_i]), float(hr_win[hi_i])
            d_t  = t_rel[hi_i:lo_i + 1] - t_rel[hi_i]      # clock from this fall's peak
            d_hr = hr_win[hi_i:lo_i + 1]
            dur, s_drop = float(d_t[-1]), float(d_hr[0] - d_hr[-1])
            if dur < GUIDE_MIN_SEC or s_drop < GUIDE_MIN_DROP:
                continue                                   # too small to be a recovery
            if s_peak_bpm < resting + MIN_PEAK_RISE:
                continue
            s_end = s_peak_t + dur

            tau = _fit_tau(d_t, d_hr, resting)

            def drop(sec, _pt=s_peak_t, _pb=s_peak_bpm, _end=s_end):
                v = _hr_at(hr_t, bpm, _pt + sec)
                return None if v is None or _pt + sec > _end else round(_pb - v)
            hrr60, hrr120 = drop(60), drop(120)

            to_base, reached = None, False
            for tr, h in zip(d_t, d_hr):
                if h <= resting + BASELINE_MARGIN:
                    to_base, reached = round(float(tr)), True
                    break

            wlo, whi = s_peak_t + 60 - RMSSD_WIN_SEC / 2, s_peak_t + 60 + RMSSD_WIN_SEC / 2
            win_rr = [v for t, v in rr_clean if wlo <= t <= whi] if whi <= s_end + 5 else []
            rmssd60 = _rmssd(win_rr)

            n = len(events) + 1
            events.append({
                "n": n, "peak_bpm": round(s_peak_bpm), "peak_t": round(s_peak_t, 1),
                "dur": round(dur), "hrr60": hrr60, "hrr120": hrr120,
                "tau": round(tau) if tau else None,
                "to_base": to_base, "reached": reached,
                "rmssd60": round(rmssd60) if rmssd60 else None,
            })
            peaks.append({"n": n, "t": round(s_peak_t, 1), "bpm": round(s_peak_bpm)})
            windows.append({"n": n, "t0": round(s_peak_t, 1), "t1": round(s_end, 1)})

            # Decay line: fitted exponential when τ is trustworthy, else a straight
            # peak→low guide (both span this fall only; every kept fall draws one).
            if tau:
                tt = np.linspace(0, dur, 40)
                curve = resting + (s_peak_bpm - resting) * np.exp(-tt / tau)
                decays.append([{"t": round(s_peak_t + float(a), 1), "bpm": round(float(b), 1)}
                               for a, b in zip(tt, curve)])
                decay_fitted.append(True)
            else:
                decays.append([{"t": round(s_peak_t, 1), "bpm": round(s_peak_bpm, 1)},
                               {"t": round(s_end, 1), "bpm": round(float(d_hr[-1]), 1)}])
                decay_fitted.append(False)

    return {
        "resting": resting, "resting_rmssd": RESTING_RMSSD_MS,
        "events": events,
        "overlay": {"peaks": peaks, "windows": windows,
                    "decays": decays, "decay_fitted": decay_fitted},
    }
