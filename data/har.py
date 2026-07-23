#!/usr/bin/env python3
"""Human Activity Recognition on Polar H10 accelerometer data.

Reads the accelerometer stream that the ESP32 pushed into hr_data.db, slices it
into short windows, extracts motion features, and trains a model that can tell
what you were doing (walking / sitting / running / ...) from raw x,y,z samples.

The workflow mirrors how you already record data:

  1. COLLECT   Record one labeled session per activity from the control page.
               (Start a session named "walking", walk; stop. Then "sitting", etc.)

  2. TRAIN     python har.py train
               Reads every labeled session, builds windows, trains a model,
               and reports leave-one-session-out accuracy so the score is honest.

  3. CLASSIFY  python har.py classify            # most recent session
               python har.py classify 9          # a specific session id
               Prints a second-by-second timeline of predicted activity
               plus an overall verdict.

Other commands:
  python har.py list        # show sessions, labels, and how much ACC data each has
  python har.py --db dumps/foo.db train

Design notes:
  * Samples are ordered by rowid (arrival order). The H10 stamps a whole batch
    with one t_ms, so per-sample time isn't reliable — we window by sample count
    at the known fixed rate instead.
  * Windows overlap 50% during training for more examples; classify uses
    non-overlapping windows so the timeline reads cleanly.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE_HZ = 25          # must match ACC_SAMPLE_RATE in esp32/src/config.h
WINDOW_SEC     = 2.0         # length of one classified window
WINDOW_LEN     = int(SAMPLE_RATE_HZ * WINDOW_SEC)   # samples per window (50)
TRAIN_STEP     = WINDOW_LEN // 2                     # 50% overlap for training
MIN_WINDOWS    = 1           # skip sessions too short to yield a window

DEFAULT_DB    = Path(__file__).parent / "hr_data.db"
DEFAULT_MODEL = Path(__file__).parent / "har_model.joblib"


# ── Feature extraction ─────────────────────────────────────────────────────────
FEATURE_NAMES = [
    "mag_mean", "mag_std", "mag_min", "mag_max", "mag_range",
    "x_mean", "y_mean", "z_mean",
    "x_std", "y_std", "z_std",
    "sma",              # signal magnitude area: overall movement intensity
    "energy",           # mean squared magnitude (AC): how vigorous
    "jerk_std",         # std of sample-to-sample magnitude change: choppiness
    "peaks_per_sec",    # dominant cadence — steps/strides show up here
    "corr_xy", "corr_xz", "corr_yz",
]


def window_features(win: np.ndarray) -> list[float]:
    """Turn one window of shape (N, 3) [x,y,z in milli-g] into a feature vector."""
    x, y, z = win[:, 0], win[:, 1], win[:, 2]
    mag = np.sqrt(x * x + y * y + z * z)

    mag_ac = mag - mag.mean()                 # remove gravity/DC so posture ≠ motion
    jerk   = np.diff(mag)

    # Count peaks in the gravity-removed magnitude → rough step/stride cadence.
    thresh = mag_ac.std()
    peaks  = 0
    for i in range(1, len(mag_ac) - 1):
        if mag_ac[i] > mag_ac[i - 1] and mag_ac[i] >= mag_ac[i + 1] and mag_ac[i] > thresh:
            peaks += 1
    peaks_per_sec = peaks / (len(win) / SAMPLE_RATE_HZ)

    def corr(a, b):
        if a.std() < 1e-6 or b.std() < 1e-6:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    return [
        float(mag.mean()), float(mag.std()), float(mag.min()), float(mag.max()),
        float(mag.max() - mag.min()),
        float(x.mean()), float(y.mean()), float(z.mean()),
        float(x.std()), float(y.std()), float(z.std()),
        float(np.abs(x).mean() + np.abs(y).mean() + np.abs(z).mean()),
        float((mag_ac ** 2).mean()),
        float(jerk.std()) if len(jerk) else 0.0,
        float(peaks_per_sec),
        corr(x, y), corr(x, z), corr(y, z),
    ]


def windows_from_samples(samples: np.ndarray, step: int):
    """Yield (start_index, feature_vector) for each window in a sample array."""
    n = len(samples)
    for start in range(0, n - WINDOW_LEN + 1, step):
        win = samples[start:start + WINDOW_LEN]
        yield start, window_features(win)


# ── Data loading ───────────────────────────────────────────────────────────────
def load_session_acc(conn, session_id) -> np.ndarray:
    """All ACC samples for a session, ordered by arrival, as an (N,3) array."""
    df = pd.read_sql(
        "SELECT x, y, z FROM acc WHERE session=? ORDER BY id", conn, params=(session_id,)
    )
    return df.to_numpy(dtype=float)


def labeled_sessions(conn):
    """Sessions that have a non-empty label AND accelerometer data."""
    rows = conn.execute(
        """SELECT s.id, s.label
             FROM sessions s
            WHERE s.label IS NOT NULL AND TRIM(s.label) <> ''
              AND EXISTS (SELECT 1 FROM acc WHERE session = s.id)
            ORDER BY s.id"""
    ).fetchall()
    return [(r[0], r[1].strip()) for r in rows]


def build_dataset(conn):
    """Return (X, y, groups, label_list) built from every labeled session.

    groups holds the session id per window so cross-validation can hold out
    whole sessions (never train and test on the same recording)."""
    X, y, groups = [], [], []
    for sid, label in labeled_sessions(conn):
        samples = load_session_acc(conn, sid)
        count = 0
        for _, feats in windows_from_samples(samples, TRAIN_STEP):
            X.append(feats)
            y.append(label)
            groups.append(sid)
            count += 1
        print(f"  session {sid:>3}  {label:<16} {len(samples):>6} samples → {count} windows")
    return np.array(X), np.array(y), np.array(groups)


# ── Commands ───────────────────────────────────────────────────────────────────
def cmd_list(conn, _args):
    rows = conn.execute(
        """SELECT s.id, s.label, s.started, s.ended,
                  (SELECT COUNT(*) FROM acc WHERE session=s.id) AS acc_rows
             FROM sessions s ORDER BY s.id"""
    ).fetchall()
    if not rows:
        print("No sessions recorded yet.")
        return
    print(f"{'id':>3}  {'label':<18} {'acc':>7}  {'~sec':>6}  started")
    print("-" * 66)
    for sid, label, started, ended, acc_rows in rows:
        secs = acc_rows / SAMPLE_RATE_HZ
        flag = "" if (label and label.strip()) else "  (no label)"
        print(f"{sid:>3}  {(label or ''):<18} {acc_rows:>7}  {secs:>6.0f}  {started}{flag}")
    print("\nTrainable = labeled sessions with ACC data. Run:  python har.py train")


def cmd_train(conn, args):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import LeaveOneGroupOut, cross_val_predict
    from sklearn.metrics import classification_report, confusion_matrix
    import joblib

    sessions = labeled_sessions(conn)
    if not sessions:
        sys.exit("No labeled sessions with ACC data yet. Record some, then re-run.\n"
                 "Tip: python har.py list")

    print("Building windows from labeled sessions:")
    X, y, groups = build_dataset(conn)
    if len(X) == 0:
        sys.exit("Sessions found but none long enough for a 2s window.")

    labels = sorted(set(y))
    print(f"\nDataset: {len(X)} windows, {len(labels)} classes {labels}")
    counts = {lab: int((y == lab).sum()) for lab in labels}
    print("Windows per class:", counts)

    clf = RandomForestClassifier(
        n_estimators=200, min_samples_leaf=2, class_weight="balanced", random_state=0
    )

    n_sessions_per_label = {}
    for sid, lab in sessions:
        n_sessions_per_label[lab] = n_sessions_per_label.get(lab, 0) + 1
    can_cv = len(set(groups)) > 1 and all(v >= 1 for v in counts.values())

    if can_cv and len(set(groups)) >= 2:
        # Leave-one-session-out: the honest score for "will this work on a new recording?"
        print("\nLeave-one-session-out cross-validation:")
        try:
            y_pred = cross_val_predict(clf, X, y, groups=groups, cv=LeaveOneGroupOut())
            print(classification_report(y, y_pred, zero_division=0))
            print("Confusion matrix (rows = truth, cols = predicted):")
            print("labels:", labels)
            print(confusion_matrix(y, y_pred, labels=labels))
        except Exception as e:
            print(f"  (skipped CV: {e})")
    else:
        print("\n(Only one session per class — can't cross-validate yet. "
              "Record a 2nd session of each activity for an honest accuracy score.)")

    clf.fit(X, y)   # final model trained on everything
    importances = sorted(zip(FEATURE_NAMES, clf.feature_importances_),
                         key=lambda t: -t[1])
    print("\nTop features the model relies on:")
    for name, imp in importances[:6]:
        print(f"  {name:<14} {imp:.3f}")

    joblib.dump({"model": clf, "labels": labels, "features": FEATURE_NAMES,
                 "sample_rate": SAMPLE_RATE_HZ, "window_len": WINDOW_LEN}, args.model)
    print(f"\nSaved model → {args.model}")
    print("Now classify new data:  python har.py classify")


def cmd_classify(conn, args):
    import joblib
    if not Path(args.model).exists():
        sys.exit(f"No model at {args.model}. Train one first:  python har.py train")
    bundle = joblib.load(args.model)
    clf, labels = bundle["model"], bundle["labels"]

    # Which session to classify
    if args.session is not None:
        sid = args.session
        row = conn.execute("SELECT label FROM sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            sys.exit(f"No session with id {sid}.")
    else:
        row = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            sys.exit("No sessions recorded.")
        sid = row[0]

    samples = load_session_acc(conn, sid)
    if len(samples) < WINDOW_LEN:
        sys.exit(f"Session {sid} has only {len(samples)} samples "
                 f"(<{WINDOW_LEN} needed for one {WINDOW_SEC:.0f}s window).")

    true_label = conn.execute("SELECT label FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    print(f"Classifying session {sid}"
          + (f"  (recorded label: {true_label!r})" if true_label else "")
          + f"  —  {len(samples)} samples ≈ {len(samples)/SAMPLE_RATE_HZ:.0f}s\n")

    feats, starts = [], []
    for start, f in windows_from_samples(samples, WINDOW_LEN):   # no overlap
        feats.append(f)
        starts.append(start)
    feats = np.array(feats)

    preds  = clf.predict(feats)
    proba  = clf.predict_proba(feats)
    conf   = proba.max(axis=1)

    print(f"{'time':>10}   activity            confidence")
    print("-" * 44)
    for start, pred, c in zip(starts, preds, conf):
        t0 = start / SAMPLE_RATE_HZ
        t1 = (start + WINDOW_LEN) / SAMPLE_RATE_HZ
        bar = "█" * int(c * 20)
        print(f"{t0:>5.0f}-{t1:<4.0f}s  {pred:<18}  {c*100:3.0f}% {bar}")

    # Overall verdict: which activity dominates the recording
    vals, cnts = np.unique(preds, return_counts=True)
    order = np.argsort(-cnts)
    print("\nOverall:")
    for i in order:
        pct = 100 * cnts[i] / len(preds)
        print(f"  {vals[i]:<18} {pct:5.1f}%  of the time")
    print(f"\n→ Best guess: {vals[order[0]]}")


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Activity recognition on Polar ACC data.")
    p.add_argument("--db", default=str(DEFAULT_DB), help="path to hr_data.db")
    p.add_argument("--model", default=str(DEFAULT_MODEL), help="path to model file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show sessions and how much ACC data each has")
    sub.add_parser("train", help="train the activity model on labeled sessions")
    c = sub.add_parser("classify", help="predict activity for a session")
    c.add_argument("session", nargs="?", type=int,
                   help="session id (default: most recent)")

    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        {"list": cmd_list, "train": cmd_train, "classify": cmd_classify}[args.cmd](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
