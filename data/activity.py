#!/usr/bin/env python3
"""Guess the activity — walk / jog / run / sprint — from Polar H10 motion data.

Companion to steps.py:
    steps.py     answers "how many steps?"   (a number)
    activity.py  answers "what activity?"     (a bucket: walk/jog/run/sprint)

────────────────────────────────────────────────────────────────────────────────
TWO PLACES DATA LIVES  (this is the whole mental model)

    THE DUMP      data/hr_data.db
                  The latest data you pulled off the Pi. Temporary — every run
                  of dump-pi.sh OVERWRITES it. Don't keep training data here.

    THE LIBRARY   data/labeled_data/labeled_walks.db
                  Your permanent, growing collection of labeled example walks.
                  Safe from dump-pi.sh. This is what the guesser learns from.

────────────────────────────────────────────────────────────────────────────────
THE WORKFLOW  (five commands, in the order you use them)

    1.  python activity.py add        Take the fresh DUMP and file each labeled
                                       session into the right bucket in the LIBRARY.
                                       (bucket is read from the label: any label
                                        with "walk" in it → walk, etc.)

    2.  python activity.py buckets     See how many example sessions you have in
                                       each bucket. Spot which buckets need more.

    3.  python activity.py train       Learn walk/jog/run/sprint from the LIBRARY.

    4.  python activity.py guess        Point it at a new recording and get the
        python activity.py guess 14     activity, second-by-second.

    5.  python activity.py demo         Pick a RANDOM library session, hide its
                                        label, and watch it guess. A fun sanity check.

Run `python steps.py calibrate` once first — the activity guesser reuses the
step-detector settings it saves (steps_model.json).
"""

import argparse
import json
import random
import sqlite3
from collections import namedtuple
from pathlib import Path

import numpy as np

import steps  # shared signal math: magnitude, gravity removal, peak finding

# ── Where everything lives ───────────────────────────────────────────────────────
DATA_DIR    = Path(__file__).parent
DUMP_DB     = DATA_DIR / "hr_data.db"                       # latest pull (temporary)
LIBRARY_DB  = DATA_DIR / "labeled_data" / "labeled_walks.db"  # permanent training data
STEP_PARAMS = DATA_DIR / "steps_model.json"                 # from `steps.py calibrate`
CLF_FILE    = DATA_DIR / "labeled_data" / "activity_model.joblib"  # the trained guesser

# ── The four buckets, slowest → fastest ──────────────────────────────────────────
BUCKETS = ["walk", "jog", "run", "sprint"]

# ── How motion is described to the guesser ───────────────────────────────────────
WINDOW_SEC    = 2.0                                          # one analyzed slice
FEATURE_NAMES = ["cadence", "intensity", "footfall_punch"]

# A session, with everything we care about in one place.
Session = namedtuple("Session", "id label started acc_rows bucket")


# ════════════════════════════════════════════════════════════════════════════════
# PART 1 — labels ↔ buckets
# ════════════════════════════════════════════════════════════════════════════════
def bucket_of(label: str | None) -> str | None:
    """Which bucket a label belongs to, by keyword. 'Slow walk 30' → 'walk'. None if no match."""
    if not label:
        return None
    low = label.lower()
    for b in BUCKETS:
        if b in low:
            return b
    return None


# ════════════════════════════════════════════════════════════════════════════════
# PART 2 — reading sessions out of a database
# ════════════════════════════════════════════════════════════════════════════════
def read_sessions(conn) -> list[Session]:
    """Every session in a database, tagged with its bucket and how much ACC it has."""
    rows = conn.execute(
        """SELECT s.id, s.label, s.started,
                  (SELECT COUNT(*) FROM acc WHERE session = s.id)
             FROM sessions s ORDER BY s.id"""
    ).fetchall()
    return [Session(i, lab, started, n, bucket_of(lab)) for i, lab, started, n in rows]


def trainable(sessions: list[Session]) -> list[Session]:
    """Only the sessions we can learn from: a real bucket AND some accelerometer data."""
    return [s for s in sessions if s.bucket is not None and s.acc_rows > 0]


# ════════════════════════════════════════════════════════════════════════════════
# PART 3 — turning a session's motion into numbers (features)
# ════════════════════════════════════════════════════════════════════════════════
def load_step_params() -> dict:
    """The step-detector settings, saved by `steps.py calibrate`."""
    if not STEP_PARAMS.exists():
        raise SystemExit(f"No step settings at {STEP_PARAMS}. Run:  python steps.py calibrate")
    return json.loads(STEP_PARAMS.read_text())


def slices(samples: np.ndarray, params: dict):
    """Chop a session into 2-second slices; yield (start_index, [cadence, intensity, punch]).

    cadence         steps per minute in the slice  (fast feet → higher)
    intensity       how big the motion is          (running pounds harder than walking)
    footfall_punch  average height of each step's bump
    """
    sr = params["sample_rate"]
    win = int(sr * WINDOW_SEC)
    if len(samples) < win:
        return
    sig = steps.step_signal(samples)                     # gravity-free motion wave
    for start in range(0, len(sig) - win + 1, win):
        chunk = sig[start:start + win]
        peaks = steps.detect_peaks(chunk, params["thresh_k"], params["min_dist"])
        cadence   = 60 * len(peaks) / WINDOW_SEC
        intensity = float(chunk.std())
        punch     = float(np.mean(chunk[peaks])) if peaks else 0.0
        yield start, [cadence, intensity, punch]


# ════════════════════════════════════════════════════════════════════════════════
# PART 4 — the commands
# ════════════════════════════════════════════════════════════════════════════════
def cmd_add(args):
    """Copy labeled sessions from the DUMP into the LIBRARY, filed by bucket."""
    if not DUMP_DB.exists():
        raise SystemExit(f"No dump at {DUMP_DB}. Pull data first (./dump-pi.sh).")
    LIBRARY_DB.parent.mkdir(parents=True, exist_ok=True)

    lib = sqlite3.connect(LIBRARY_DB)
    lib.execute("ATTACH DATABASE ? AS dump", (str(DUMP_DB),))

    # Sessions already saved (matched by their start time) so we never double-add.
    have = {r[0] for r in lib.execute("SELECT started FROM sessions").fetchall()}

    # Read the dump's sessions (through the attached 'dump' database).
    rows = lib.execute(
        """SELECT s.id, s.label, s.started,
                  (SELECT COUNT(*) FROM dump.acc WHERE session = s.id)
             FROM dump.sessions s ORDER BY s.id"""
    ).fetchall()

    # Optional manual override: `add <id> --as run` forces one session into a bucket
    # even if its label has no keyword.
    forced = args.as_bucket
    if forced and forced not in BUCKETS:
        raise SystemExit(f"--as must be one of {BUCKETS}")

    added, skipped = [], []
    for sid, label, started, acc_rows in rows:
        if args.session is not None and sid != args.session:
            continue
        bucket = forced or bucket_of(label)
        reason = None
        if acc_rows == 0:
            reason = "no accelerometer data"
        elif bucket is None:
            reason = "label has no walk/jog/run/sprint keyword (use --as)"
        elif started in have:
            reason = "already in library"
        if reason:
            skipped.append((sid, label, reason))
            continue

        # If forcing a bucket, make the stored label carry the keyword so training sees it.
        store_label = label if bucket_of(label) == bucket else f"{label or 'sample'} [{bucket}]"
        cur = lib.execute(
            "INSERT INTO sessions (started, ended, label) "
            "SELECT started, ended, ? FROM dump.sessions WHERE id = ?", (store_label, sid))
        new_id = cur.lastrowid
        lib.execute(
            "INSERT INTO acc (received, t_ms, x, y, z, session) "
            "SELECT received, t_ms, x, y, z, ? FROM dump.acc WHERE session = ?", (new_id, sid))
        lib.execute(
            "INSERT INTO readings (received, t_ms, bpm, rr_ms, session) "
            "SELECT received, t_ms, bpm, rr_ms, ? FROM dump.readings WHERE session = ?",
            (new_id, sid))
        have.add(started)
        added.append((new_id, store_label, bucket, acc_rows))

    lib.commit()
    lib.close()

    if added:
        print("Added to the library:")
        for new_id, label, bucket, acc_rows in added:
            print(f"  #{new_id:<3} {label!r:<22} → {bucket:<7} ({acc_rows} samples)")
    else:
        print("Nothing new to add.")
    if skipped:
        print("\nSkipped:")
        for sid, label, reason in skipped:
            print(f"  dump #{sid:<3} {label!r:<22} — {reason}")
    print("\nNext:  python activity.py buckets")


def cmd_buckets(args):
    """Show, per bucket, which example sessions the library holds."""
    conn = sqlite3.connect(LIBRARY_DB)
    params = load_step_params()
    sessions = trainable(read_sessions(conn))
    sr = params["sample_rate"]

    print(f"LIBRARY: {LIBRARY_DB}\n")
    for bucket in BUCKETS:
        mine = [s for s in sessions if s.bucket == bucket]
        secs = sum(s.acc_rows for s in mine) / sr
        head = f"{bucket.upper():<7} {len(mine)} session(s), {secs:.0f}s total"
        if not mine:
            print(f"{head}   ← empty, record some")
        else:
            print(head)
            for s in mine:
                print(f"    #{s.id:<3} {s.label:<20} {s.acc_rows/sr:>4.0f}s")
    conn.close()
    print("\nAim for a few sessions in each bucket, then:  python activity.py train")


def cmd_train(args):
    """Learn walk/jog/run/sprint from every session in the library."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import LeaveOneGroupOut, cross_val_predict
    from sklearn.metrics import classification_report, confusion_matrix
    import joblib

    conn = sqlite3.connect(LIBRARY_DB)
    params = load_step_params()
    sessions = trainable(read_sessions(conn))
    if not sessions:
        raise SystemExit("Library is empty. Add some labeled walks:  python activity.py add")

    # Build the examples: one row per 2-second slice.
    X, y, groups = [], [], []
    print("Learning from:")
    for s in sessions:
        samples = steps.load_session_acc(conn, s.id)
        n = 0
        for _, feats in slices(samples, params):
            X.append(feats); y.append(s.bucket); groups.append(s.id); n += 1
        print(f"  #{s.id:<3} {s.label!r:<20} → {s.bucket:<7} ({n} slices)")
    conn.close()
    X, y, groups = np.array(X), np.array(y), np.array(groups)

    present = sorted(set(y), key=BUCKETS.index)
    empty   = [b for b in BUCKETS if b not in present]
    print(f"\nBuckets with data : {present}")
    if empty:
        print(f"Buckets still empty: {empty}  (record labeled sessions to fill them)")

    clf = RandomForestClassifier(n_estimators=200, min_samples_leaf=2,
                                 class_weight="balanced", random_state=0)

    # Honest score: hide one whole session at a time and guess it from the rest.
    if len(set(groups)) > 1 and len(present) > 1:
        print("\nHonest accuracy (guessing sessions it never trained on):")
        try:
            pred = cross_val_predict(clf, X, y, groups=groups, cv=LeaveOneGroupOut())
            print(classification_report(y, pred, zero_division=0))
            print("Confusion (rows = actual, cols = guessed):", present)
            print(confusion_matrix(y, pred, labels=present))
        except Exception as e:
            print(f"  (couldn't score yet: {e})")
    else:
        print("\n(Need ≥2 sessions across ≥2 buckets for an honest score.)")

    clf.fit(X, y)
    joblib.dump({"model": clf, "buckets": present, "features": FEATURE_NAMES,
                 "step_params": params}, CLF_FILE)
    print(f"\nSaved guesser → {CLF_FILE}")
    print("Try it:  python activity.py guess   |   python activity.py demo")


def _predict(bundle, samples) -> tuple[str, float, list]:
    """Run the guesser on one session's samples → (overall_bucket, share%, per-slice preds)."""
    params = bundle["step_params"]
    feats = [f for _, f in slices(samples, params)]
    if not feats:
        raise SystemExit("Recording too short to guess.")
    preds = bundle["model"].predict(np.array(feats))
    conf  = bundle["model"].predict_proba(np.array(feats)).max(axis=1)
    vals, cnts = np.unique(preds, return_counts=True)
    overall = vals[np.argmax(cnts)]
    share = 100 * cnts.max() / len(preds)
    return overall, share, list(zip(preds, conf))


def _load_guesser():
    import joblib
    if not CLF_FILE.exists():
        raise SystemExit(f"No trained guesser. Run:  python activity.py train")
    return joblib.load(CLF_FILE)


def cmd_guess(args):
    """Guess the activity of a recording (from the dump by default), slice by slice."""
    bundle = _load_guesser()
    db = args.db or DUMP_DB
    conn = sqlite3.connect(db)
    row = (conn.execute("SELECT id, label FROM sessions WHERE id=?", (args.session,)).fetchone()
           if args.session is not None else
           conn.execute("SELECT id, label FROM sessions ORDER BY id DESC LIMIT 1").fetchone())
    if row is None:
        raise SystemExit(f"No such session in {db}.")
    sid, label = row
    samples = steps.load_session_acc(conn, sid)
    conn.close()

    overall, share, per_slice = _predict(bundle, samples)
    print(f"Session {sid}" + (f"  (label: {label!r})" if label else "") + "\n")
    print(f"{'time':>9}   activity   sure?")
    print("-" * 32)
    for i, (pred, c) in enumerate(per_slice):
        print(f"{i*2:>3}-{i*2+2:<3}s   {pred:<8}   {c*100:3.0f}%")
    print(f"\n→ Best guess: {overall.upper()}  ({share:.0f}% of the recording)")


def cmd_demo(args):
    """Pick a random library session, hide its label, and guess it."""
    bundle = _load_guesser()
    conn = sqlite3.connect(LIBRARY_DB)
    sessions = trainable(read_sessions(conn))
    if not sessions:
        raise SystemExit("Library is empty. Add some walks first:  python activity.py add")
    s = random.choice(sessions)
    samples = steps.load_session_acc(conn, s.id)
    conn.close()

    overall, share, _ = _predict(bundle, samples)
    correct = (overall == s.bucket)
    print(f"Random pick: session #{s.id}  ({s.acc_rows/bundle['step_params']['sample_rate']:.0f}s)")
    print(f"  It guessed : {overall.upper()}  ({share:.0f}% of the recording)")
    print(f"  Actually   : {s.bucket.upper()}   (label {s.label!r})")
    print("  " + ("✓ correct!" if correct else "✗ missed — this bucket needs more examples"))


# ════════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="Guess walk/jog/run/sprint from ACC data.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="file the latest dump's labeled sessions into the library")
    a.add_argument("session", nargs="?", type=int, help="only add this dump session id")
    a.add_argument("--as", dest="as_bucket", help="force into a bucket: walk/jog/run/sprint")

    sub.add_parser("buckets", help="show how many example sessions each bucket has")
    sub.add_parser("train", help="learn walk/jog/run/sprint from the library")

    g = sub.add_parser("guess", help="guess the activity of a recording")
    g.add_argument("session", nargs="?", type=int, help="session id (default: most recent)")
    g.add_argument("--db", help="database to read (default: the dump, hr_data.db)")

    sub.add_parser("demo", help="guess a random library session and reveal the answer")

    args = p.parse_args()
    {"add": cmd_add, "buckets": cmd_buckets, "train": cmd_train,
     "guess": cmd_guess, "demo": cmd_demo}[args.cmd](args)


if __name__ == "__main__":
    main()
