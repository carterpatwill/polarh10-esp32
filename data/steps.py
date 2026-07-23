#!/usr/bin/env python3
"""Step counting from Polar H10 accelerometer data.

Where har.py answers "what activity is this?", this answers "how many steps?".

Every footfall is a bump in the accelerometer magnitude. Remove gravity and a
walk turns into a clean wave — one crest per step. So counting steps is just
counting crests. The only tricky part is knowing *how big* a bump counts as a
real step and *how close* two steps can be — and that is exactly what we learn
from the sessions where you tell us the true count.

────────────────────────────────────────────────────────────────────────────────
HOW TO USE IT

  1. RECORD   Start a session, walk a KNOWN number of steps, stop.
              Put that number in the label:  "walk 30", "30 steps", "jog 50".
              Do this a few times, at a few speeds, for a good calibration.

  2. CALIBRATE  python steps.py calibrate
              Reads every session whose label contains a number, tunes the
              detector so its counts best match your real counts, and reports
              how far off it is. Saves the tuned settings.

  3. COUNT    python steps.py count            # latest session
              python steps.py count 12         # a specific session id
              Detects and counts steps on a new (unlabeled) walk.

  4. SEE IT   python steps.py plot             # latest session
              python steps.py plot 12
              Draws the acceleration wave with every detected step marked, so
              you can eyeball whether it's finding real steps.

Other:
  python steps.py list        # sessions, labels, ACC amount, parsed true count

────────────────────────────────────────────────────────────────────────────────
WHY NOT A NEURAL NET?

For counting a repeating motion, filtered peak-detection is what real pedometers
do — it is more robust, needs almost no data, and you can SEE why it counted what
it did. The "machine learning" here is fitting two interpretable numbers
(bump size + minimum spacing) to your labeled walks. That is genuine learning,
just the honest, debuggable kind.
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE_HZ = 25          # must match ACC_SAMPLE_RATE in esp32/src/config.h
SMOOTH_WIN     = 5           # samples (~0.2s) — denoise the magnitude a little
GRAVITY_WIN    = 25          # samples (~1.0s) — moving average = gravity/drift baseline

DEFAULT_DB     = Path(__file__).parent / "hr_data.db"
DEFAULT_MODEL  = Path(__file__).parent / "steps_model.json"

# What "a step" is allowed to look like, searched during calibration:
#   THRESH_K  — a step's bump must exceed k × (signal's own std). Adapts to how
#               hard you're moving, so it generalizes across gentle vs vigorous walks.
#   MIN_DIST  — minimum samples between two steps (caps the fastest believable cadence).
THRESH_K_GRID  = np.round(np.arange(0.25, 1.55, 0.05), 3)
MIN_DIST_GRID  = list(range(7, 19))     # 7 samples ≈ 3.6 steps/s … 18 ≈ 1.4 steps/s


# ── Signal processing ────────────────────────────────────────────────────────────
def moving_average(a: np.ndarray, w: int) -> np.ndarray:
    """Centered moving average, same length as input (edges shrink the window)."""
    if w <= 1 or len(a) < 2:
        return a.astype(float)
    kernel = np.ones(w) / w
    smoothed = np.convolve(a, kernel, mode="same")
    # Fix the edges, where 'same' convolution divides by too-few real samples.
    csum = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    for i in range(len(a)):
        lo, hi = max(0, i - w // 2), min(len(a), i + w // 2 + 1)
        smoothed[i] = (csum[hi] - csum[lo]) / (hi - lo)
    return smoothed


def step_signal(samples: np.ndarray) -> np.ndarray:
    """Turn raw (N,3) x,y,z [milli-g] into a gravity-free oscillation around zero.

    One crest of this signal ≈ one footfall."""
    x, y, z = samples[:, 0], samples[:, 1], samples[:, 2]
    mag = np.sqrt(x * x + y * y + z * z)
    mag = moving_average(mag, SMOOTH_WIN)          # denoise
    baseline = moving_average(mag, GRAVITY_WIN)    # gravity + slow drift
    return mag - baseline                          # walking wave, centered on 0


def detect_peaks(sig: np.ndarray, thresh_k: float, min_dist: int) -> list[int]:
    """Indices of step peaks: local maxima above k×std, at least min_dist apart.

    If two candidates fall within min_dist, we keep the taller one — so a single
    step's double-bump doesn't get counted twice."""
    if len(sig) < 3:
        return []
    height = thresh_k * sig.std()
    peaks: list[int] = []
    for i in range(1, len(sig) - 1):
        if sig[i] > height and sig[i] >= sig[i - 1] and sig[i] > sig[i + 1]:
            if peaks and i - peaks[-1] < min_dist:
                if sig[i] > sig[peaks[-1]]:        # closer bump is taller → replace
                    peaks[-1] = i
            else:
                peaks.append(i)
    return peaks


def count_steps(samples: np.ndarray, params: dict) -> tuple[int, np.ndarray, list[int]]:
    """Return (step_count, step_signal, peak_indices) for one session's samples."""
    sig = step_signal(samples)
    peaks = detect_peaks(sig, params["thresh_k"], params["min_dist"])
    return len(peaks), sig, peaks


# ── Data loading ───────────────────────────────────────────────────────────────
def load_session_acc(conn, session_id) -> np.ndarray:
    """All ACC samples for a session, in arrival order, as an (N,3) float array."""
    df = pd.read_sql(
        "SELECT x, y, z FROM acc WHERE session=? ORDER BY id", conn, params=(session_id,)
    )
    return df.to_numpy(dtype=float)


def true_count_from_label(label) -> int | None:
    """Parse the ground-truth step count from a label. Uses the LAST integer,
    so 'walk 30', '30 steps', 'jog50', 'lap 2 - 40 steps' all work."""
    if not label:
        return None
    nums = re.findall(r"\d+", label)
    return int(nums[-1]) if nums else None


def all_sessions(conn):
    """(id, label, acc_row_count) for every session, oldest first."""
    return conn.execute(
        """SELECT s.id, s.label,
                  (SELECT COUNT(*) FROM acc WHERE session=s.id) AS acc_rows
             FROM sessions s ORDER BY s.id"""
    ).fetchall()


def calibration_sessions(conn):
    """(id, label, true_count, samples) for sessions that have BOTH a numeric
    label (the truth) AND accelerometer data to learn from."""
    out = []
    for sid, label, acc_rows in all_sessions(conn):
        truth = true_count_from_label(label)
        if truth is None or acc_rows == 0:
            continue
        out.append((sid, label, truth, load_session_acc(conn, sid)))
    return out


# ── Commands ───────────────────────────────────────────────────────────────────
def cmd_list(conn, _args):
    rows = all_sessions(conn)
    if not rows:
        print("No sessions recorded yet.")
        return
    print(f"{'id':>3}  {'label':<20} {'acc':>7} {'~sec':>6}  {'true steps':>10}")
    print("-" * 56)
    for sid, label, acc_rows in rows:
        secs = acc_rows / SAMPLE_RATE_HZ
        truth = true_count_from_label(label)
        truth_s = str(truth) if truth is not None else "—"
        print(f"{sid:>3}  {(label or ''):<20} {acc_rows:>7} {secs:>6.0f}  {truth_s:>10}")
    print("\nCalibratable = has a number in the label AND accelerometer data.")
    print("Record a few, then:  python steps.py calibrate")


def cmd_calibrate(conn, args):
    sessions = calibration_sessions(conn)
    if not sessions:
        sys.exit(
            "No calibration sessions yet.\n"
            "Record a walk of a KNOWN step count and put the number in the label\n"
            '(e.g. "walk 30"), then re-run.   See:  python steps.py list'
        )

    print(f"Calibrating on {len(sessions)} labeled session(s):")
    for sid, label, truth, samples in sessions:
        print(f"  session {sid:>3}  {label!r:<22} truth={truth:<4} "
              f"({len(samples)} samples ≈ {len(samples)/SAMPLE_RATE_HZ:.0f}s)")

    # Pre-compute each session's step signal once; grid-search the two knobs.
    prepped = [(step_signal(s), truth) for _, _, truth, s in sessions]

    best = None   # (mean_abs_error, thresh_k, min_dist)
    for k in THRESH_K_GRID:
        for d in MIN_DIST_GRID:
            errs = [abs(len(detect_peaks(sig, k, d)) - truth) for sig, truth in prepped]
            mae = float(np.mean(errs))
            # Prefer lower error; tie-break toward a larger min_dist (more robust).
            key = (mae, -d)
            if best is None or key < (best[0], -best[2]):
                best = (mae, float(k), int(d))

    mae, thresh_k, min_dist = best
    params = {
        "thresh_k": thresh_k,
        "min_dist": min_dist,
        "smooth_win": SMOOTH_WIN,
        "gravity_win": GRAVITY_WIN,
        "sample_rate": SAMPLE_RATE_HZ,
    }

    print(f"\nBest settings:  bump > {thresh_k:.2f}×std,  "
          f"min {min_dist} samples apart (≤{SAMPLE_RATE_HZ/min_dist:.1f} steps/s)")
    print(f"Average miss:   {mae:.1f} steps per session\n")
    print(f"{'id':>3}  {'label':<20} {'true':>5} {'counted':>8}  error")
    print("-" * 50)
    for (sid, label, truth, _), (sig, _) in zip(sessions, prepped):
        got = len(detect_peaks(sig, thresh_k, min_dist))
        print(f"{sid:>3}  {(label or ''):<20} {truth:>5} {got:>8}  {got - truth:+d}")

    Path(args.model).write_text(json.dumps(params, indent=2))
    print(f"\nSaved settings → {args.model}")
    print("Now count a new walk:  python steps.py count")


def _load_params(model_path) -> dict:
    if not Path(model_path).exists():
        sys.exit(f"No calibration at {model_path}. Run first:  python steps.py calibrate")
    return json.loads(Path(model_path).read_text())


def _pick_session(conn, session_arg) -> int:
    if session_arg is not None:
        if conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_arg,)).fetchone() is None:
            sys.exit(f"No session with id {session_arg}.")
        return session_arg
    row = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        sys.exit("No sessions recorded.")
    return row[0]


def cmd_count(conn, args):
    params = _load_params(args.model)
    sid = _pick_session(conn, args.session)
    samples = load_session_acc(conn, sid)
    if len(samples) < GRAVITY_WIN:
        sys.exit(f"Session {sid} has only {len(samples)} ACC samples — too short to count.")

    label = conn.execute("SELECT label FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    truth = true_count_from_label(label)

    steps, _, peaks = count_steps(samples, params)
    secs = len(samples) / params["sample_rate"]
    cadence = 60 * steps / secs if secs else 0

    print(f"Session {sid}" + (f"  (label: {label!r})" if label else ""))
    print(f"  duration : {secs:.0f}s   ({len(samples)} ACC samples)")
    print(f"  STEPS    : {steps}")
    print(f"  cadence  : {cadence:.0f} steps/min")
    if truth is not None:
        print(f"  labeled  : {truth}   (off by {steps - truth:+d})")


def cmd_plot(conn, args):
    import matplotlib.pyplot as plt

    params = _load_params(args.model)
    sid = _pick_session(conn, args.session)
    samples = load_session_acc(conn, sid)
    if len(samples) < GRAVITY_WIN:
        sys.exit(f"Session {sid} has only {len(samples)} ACC samples — too short to plot.")

    label = conn.execute("SELECT label FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    truth = true_count_from_label(label)
    steps, sig, peaks = count_steps(samples, params)
    t = np.arange(len(sig)) / params["sample_rate"]
    height = params["thresh_k"] * sig.std()

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(t, sig, lw=1.0, color="#58a6ff", label="motion (gravity removed)")
    ax.axhline(height, color="#8b949e", ls="--", lw=1, label=f"step threshold")
    if peaks:
        ax.plot(t[peaks], sig[peaks], "o", ms=6, color="#f85149",
                label=f"detected step ({steps})")
    ax.set_xlabel("seconds")
    ax.set_ylabel("acceleration (milli-g)")
    title = f"Session {sid} — counted {steps} steps"
    if truth is not None:
        title += f"  (labeled {truth}, off by {steps - truth:+d})"
    ax.set_title(title)
    ax.legend(loc="upper right")

    # dark theme, matching analyze.py
    ax.set_facecolor("#0d1117")
    fig.patch.set_facecolor("#0d1117")
    ax.grid(True, alpha=0.2, ls="--")
    ax.tick_params(colors="white")
    for lbl in (ax.xaxis.label, ax.yaxis.label, ax.title):
        lbl.set_color("white")
    for s in ("bottom", "left"):
        ax.spines[s].set_color("#30363d")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    plt.tight_layout()
    plt.show()


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Count steps from Polar H10 ACC data.")
    p.add_argument("--db", default=str(DEFAULT_DB), help="path to hr_data.db")
    p.add_argument("--model", default=str(DEFAULT_MODEL), help="path to calibration file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show sessions, labels, and parsed true step counts")
    sub.add_parser("calibrate", help="tune the step detector on labeled sessions")
    for name, help_ in (("count", "count steps in a session"),
                        ("plot", "draw the wave with detected steps marked")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("session", nargs="?", type=int,
                       help="session id (default: most recent)")

    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        {"list": cmd_list, "calibrate": cmd_calibrate,
         "count": cmd_count, "plot": cmd_plot}[args.cmd](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
