#!/usr/bin/env python3
"""Segment a workout into what-you-were-doing-when.

Runs the trained walk/jog/run/sprint model (data/activity.py) over a whole
session in 2-second windows and returns a timeline of activity segments, so the
dashboard can draw "walking here, still there" under the accelerometer trace.

Self-contained on purpose: it re-implements the small bit of signal math from
data/steps.py (moving average → gravity-free wave → peak cadence) so the
dashboard can be deployed to the Pi without importing the data/ analysis package.
The numbers must stay in sync with data/steps.py + data/activity.py; the feature
recipe (cadence, intensity, footfall_punch per 2s window) is identical.

The model has no "still" bucket (that training bucket is empty), so it would
force a motionless stretch into walk/jog/run/sprint. We override any window whose
motion intensity is below STILL_INTENSITY to `still` before trusting the model.
"""
import numpy as np

WINDOW_SEC = 2.0          # one classified slice — matches data/activity.py
# A 2s window whose gravity-free motion (std, milli-g) is below this counts as
# "still". Walk sits ~90-180; sitting/standing is far lower. Tunable.
STILL_INTENSITY = 55.0


# ── signal math (ported verbatim from data/steps.py) ─────────────────────────
def moving_average(a: np.ndarray, w: int) -> np.ndarray:
    """Centered moving average, same length as input (edges shrink the window)."""
    if w <= 1 or len(a) < 2:
        return a.astype(float)
    kernel = np.ones(w) / w
    smoothed = np.convolve(a, kernel, mode="same")
    csum = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    for i in range(len(a)):
        lo, hi = max(0, i - w // 2), min(len(a), i + w // 2 + 1)
        smoothed[i] = (csum[hi] - csum[lo]) / (hi - lo)
    return smoothed


def step_signal(samples: np.ndarray, smooth_win: int, gravity_win: int) -> np.ndarray:
    """Raw (N,3) x,y,z [milli-g] → gravity-free oscillation (one crest ≈ one footfall)."""
    x, y, z = samples[:, 0], samples[:, 1], samples[:, 2]
    mag = np.sqrt(x * x + y * y + z * z)
    mag = moving_average(mag, smooth_win)
    baseline = moving_average(mag, gravity_win)
    return mag - baseline


def detect_peaks(sig: np.ndarray, thresh_k: float, min_dist: int) -> list:
    """Step peaks: local maxima above k×std, at least min_dist apart."""
    if len(sig) < 3:
        return []
    height = thresh_k * sig.std()
    peaks: list = []
    for i in range(1, len(sig) - 1):
        if sig[i] > height and sig[i] >= sig[i - 1] and sig[i] > sig[i + 1]:
            if peaks and i - peaks[-1] < min_dist:
                if sig[i] > sig[peaks[-1]]:
                    peaks[-1] = i
            else:
                peaks.append(i)
    return peaks


# ── windowing + features (same recipe as data/activity.py slices()) ──────────
def _window_features(chunk: np.ndarray, sp: dict) -> list:
    peaks = detect_peaks(chunk, sp["thresh_k"], sp["min_dist"])
    cadence = 60 * len(peaks) / WINDOW_SEC
    intensity = float(chunk.std())
    punch = float(np.mean(chunk[peaks])) if peaks else 0.0
    return [cadence, intensity, punch]


def _smooth_labels(labels: list) -> list:
    """Median-of-3 majority filter: a lone label between two agreeing neighbors
    is flipped to match them. Kills single-window jitter without blurring real
    transitions."""
    out = list(labels)
    for i in range(1, len(labels) - 1):
        a, b, c = labels[i - 1], labels[i], labels[i + 1]
        if a == c and b != a:
            out[i] = a
    return out


def analyze(samples: np.ndarray, bundle: dict, still_intensity: float = STILL_INTENSITY) -> dict:
    """Return {segments, totals, window_sec, still_intensity} for one session.

    segments: [{t0, t1, activity, confidence}] over elapsed seconds.
    totals:   {activity: seconds} across the whole session.
    """
    sp = bundle["step_params"]
    sr = sp["sample_rate"]
    win = int(sr * WINDOW_SEC)
    if len(samples) < win:
        return {"segments": [], "totals": {}, "window_sec": WINDOW_SEC,
                "still_intensity": still_intensity}

    sig = step_signal(samples, sp["smooth_win"], sp["gravity_win"])
    starts, feats = [], []
    for start in range(0, len(sig) - win + 1, win):
        starts.append(start)
        feats.append(_window_features(sig[start:start + win], sp))

    X = np.array(feats)
    preds = bundle["model"].predict(X)
    conf = bundle["model"].predict_proba(X).max(axis=1)

    # Still-override: low-motion windows can't be gait, whatever the model says.
    labels, confs = [], []
    for f, p, c in zip(feats, preds, conf):
        if f[1] < still_intensity:
            labels.append("still"); confs.append(1.0)
        else:
            labels.append(str(p)); confs.append(float(c))
    labels = _smooth_labels(labels)

    # Merge consecutive same-activity windows into segments; tally totals.
    segments, totals = [], {}
    for start, lab, c in zip(starts, labels, confs):
        t0, t1 = start / sr, (start + win) / sr
        totals[lab] = totals.get(lab, 0.0) + WINDOW_SEC
        if segments and segments[-1]["activity"] == lab:
            segments[-1]["t1"] = round(t1, 2)
            segments[-1]["_confs"].append(c)
        else:
            segments.append({"t0": round(t0, 2), "t1": round(t1, 2),
                             "activity": lab, "_confs": [c]})

    out_segments = [{
        "t0": s["t0"], "t1": s["t1"], "activity": s["activity"],
        "confidence": round(sum(s["_confs"]) / len(s["_confs"]), 2),
    } for s in segments]

    return {
        "segments": out_segments,
        "totals": {k: round(v, 1) for k, v in totals.items()},
        "window_sec": WINDOW_SEC,
        "still_intensity": still_intensity,
    }
