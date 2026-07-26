#!/usr/bin/env python3
"""Self-service labeling backend — powers the web labeler in the dashboard.

Everything the browser needs to grow the training library without the terminal:

    candidates()          new sessions in the DUMP you could add, each with an
                          auto-trim suggestion + a per-second intensity bar.
    library()             what the LIBRARY already holds, grouped by bucket.
    add(id, label, ...)   copy ONE dump session into the library, TRIMMED to the
                          window you chose (drops the start/stop ramp that skews
                          jog<->run). Labeled with your bucket.
    remove(id)            delete a library session (undo a bad add / clean up).
    train_and_save()      retrain from the library and return the honest report.

This reuses data/activity.py so the model recipe stays in ONE place. It only runs
on the Mac (the library + trainer live here); the Pi dashboard never imports it.
"""
import sqlite3

import numpy as np

import activity  # bucket_of, slices, load_step_params, train core, DB paths
import steps     # shared signal math (step_signal, load_session_acc)

DUMP_DB    = activity.DUMP_DB
LIBRARY_DB = activity.LIBRARY_DB
BUCKETS    = activity.BUCKETS


# ── intensity over time (the basis for both the bar chart and auto-trim) ──────────
def _intensity_per_second(samples, sr):
    """One motion-energy number per second: std of the gravity-free step signal in
    that second. Low while standing/walking-to-position, high mid-effort. This is
    the same 'intensity' feature the model uses, just bucketed to 1s for display."""
    if len(samples) < sr:
        return []
    sig = steps.step_signal(samples)
    out = []
    for start in range(0, len(sig) - sr + 1, sr):
        out.append(float(sig[start:start + sr].std()))
    return out


def auto_trim(intensity):
    """Pick the sustained-effort window, dropping the quiet start/stop ramp.

    Returns (t0, t1) in whole seconds (t1 exclusive). The idea: find how energetic
    the *sustained* part is (median of the busier half), keep the longest run that
    stays above half that level, and require it clear the 'still' floor. Falls back
    to the whole clip if there's no clear active stretch."""
    n = len(intensity)
    if n < 3:
        return 0, n
    arr = np.array(intensity)
    busy = np.median(np.sort(arr)[n // 2:])          # typical level of the active half
    thresh = max(activity.STILL_INTENSITY, 0.5 * busy)
    active = arr >= thresh

    # Longest contiguous run of active seconds.
    best_len = best_start = cur_start = 0
    cur_len = 0
    for i, a in enumerate(active):
        if a:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
        else:
            cur_len = 0
    if best_len == 0:
        return 0, n
    return best_start, best_start + best_len


# ── reading sessions out of a database ────────────────────────────────────────────
def _session_rows(conn):
    """(id, label, started, ended, acc_count) for every session, oldest first."""
    return conn.execute("""
        SELECT s.id, s.label, s.started, s.ended,
               (SELECT COUNT(*) FROM acc WHERE session = s.id) AS acc_count
        FROM sessions s ORDER BY s.id
    """).fetchall()


def candidates():
    """Dump sessions you could add: those with ACC data, newest first. Each carries
    its auto-trim suggestion and intensity bar, plus whether it's already filed."""
    if not DUMP_DB.exists():
        return {"error": f"No dump at {DUMP_DB}. Pull from the Pi first (./dump-pi.sh)."}
    sr = activity.load_step_params()["sample_rate"]
    dump = sqlite3.connect(f"file:{DUMP_DB}?mode=ro", uri=True)

    have = set()
    if LIBRARY_DB.exists():
        lib = sqlite3.connect(f"file:{LIBRARY_DB}?mode=ro", uri=True)
        have = {r[0] for r in lib.execute("SELECT started FROM sessions").fetchall()}
        lib.close()

    out = []
    for sid, label, started, ended, acc_count in _session_rows(dump):
        if not acc_count:
            continue
        samples = steps.load_session_acc(dump, sid)
        intensity = _intensity_per_second(samples, sr)
        t0, t1 = auto_trim(intensity)
        out.append({
            "id": sid, "label": label, "started": started,
            "duration_s": round(len(samples) / sr),
            "suggested_bucket": activity.bucket_of(label),
            "already_added": started in have,
            "trim": {"t0": t0, "t1": t1},
            "intensity": [round(v, 1) for v in intensity],
        })
    dump.close()
    out.reverse()   # newest first
    return {"sessions": out, "buckets": BUCKETS}


def library():
    """What the library holds, grouped by bucket, with per-session durations."""
    if not LIBRARY_DB.exists():
        return {"buckets": {b: [] for b in BUCKETS}, "total": 0}
    sr = activity.load_step_params()["sample_rate"]
    conn = sqlite3.connect(f"file:{LIBRARY_DB}?mode=ro", uri=True)
    grouped = {b: [] for b in BUCKETS}
    total = 0
    for sid, label, started, ended, acc_count in _session_rows(conn):
        if not acc_count:
            continue
        b = activity.bucket_of(label)
        if b not in grouped:
            continue
        grouped[b].append({"id": sid, "label": label,
                           "duration_s": round(acc_count / sr)})
        total += 1
    conn.close()
    return {"buckets": grouped, "total": total}


# ── writing to the library ────────────────────────────────────────────────────────
def add(dump_id, bucket, t0=None, t1=None, note=""):
    """Copy dump session `dump_id` into the library as `bucket`, keeping only the
    ACC/HR inside [t0, t1) seconds (index-based — ACC is a fixed-rate stream, so
    second = sample_index / sample_rate). Returns the new library row info."""
    if bucket not in BUCKETS:
        return {"error": f"bucket must be one of {BUCKETS}"}
    if not DUMP_DB.exists():
        return {"error": "no dump database"}
    sr = activity.load_step_params()["sample_rate"]
    LIBRARY_DB.parent.mkdir(parents=True, exist_ok=True)

    lib = sqlite3.connect(LIBRARY_DB)
    lib.execute("ATTACH DATABASE ? AS dump", (str(DUMP_DB),))
    src = lib.execute(
        "SELECT started, ended, label FROM dump.sessions WHERE id = ?", (dump_id,)).fetchone()
    if src is None:
        lib.close()
        return {"error": f"no dump session #{dump_id}"}
    started, ended, orig_label = src

    if {r[0] for r in lib.execute("SELECT started FROM sessions WHERE started = ?", (started,))}:
        lib.close()
        return {"error": "already in library (remove it first to re-add with a new trim)"}

    # Make the stored label carry the bucket keyword so training reads it back.
    base = (note or orig_label or "sample").strip()
    label = base if activity.bucket_of(base) == bucket else f"{base} [{bucket}]"

    cur = lib.execute("INSERT INTO sessions (started, ended, label) VALUES (?,?,?)",
                      (started, ended, label))
    new_id = cur.lastrowid

    # Trim window in ACC sample indices (t1 exclusive). None = keep everything.
    lo = int(t0 * sr) if t0 is not None else 0
    hi = int(t1 * sr) if t1 is not None else None
    acc = lib.execute(
        "SELECT received, t_ms, x, y, z FROM dump.acc WHERE session = ? ORDER BY id",
        (dump_id,)).fetchall()
    kept_acc = acc[lo:hi]
    lib.executemany(
        "INSERT INTO acc (received, t_ms, x, y, z, session) VALUES (?,?,?,?,?,?)",
        [(*row, new_id) for row in kept_acc])

    # HR has real per-reading time; trim it by elapsed seconds against the same window.
    hr = lib.execute(
        "SELECT received, t_ms, bpm, rr_ms FROM dump.hr WHERE session = ? ORDER BY id",
        (dump_id,)).fetchall()
    if hr:
        hr0 = min(r[1] for r in hr)
        lo_s = t0 if t0 is not None else 0
        hi_s = t1 if t1 is not None else float("inf")
        kept_hr = [r for r in hr if lo_s <= (r[1] - hr0) / 1000.0 < hi_s]
        lib.executemany(
            "INSERT INTO hr (received, t_ms, bpm, rr_ms, session) VALUES (?,?,?,?,?)",
            [(*row, new_id) for row in kept_hr])

    lib.commit()
    lib.close()
    return {"id": new_id, "label": label, "bucket": bucket,
            "kept_seconds": round(len(kept_acc) / sr)}


def remove(lib_id):
    """Delete a library session and its samples (undo a bad add)."""
    if not LIBRARY_DB.exists():
        return {"error": "no library database"}
    lib = sqlite3.connect(LIBRARY_DB)
    row = lib.execute("SELECT id FROM sessions WHERE id = ?", (lib_id,)).fetchone()
    if row is None:
        lib.close()
        return {"error": f"no library session #{lib_id}"}
    lib.execute("DELETE FROM acc WHERE session = ?", (lib_id,))
    lib.execute("DELETE FROM hr  WHERE session = ?", (lib_id,))
    lib.execute("DELETE FROM sessions WHERE id = ?", (lib_id,))
    lib.commit()
    lib.close()
    return {"removed": lib_id}


def train_and_save():
    """Retrain from the whole library, save the model, return the honest report."""
    import joblib
    conn = sqlite3.connect(LIBRARY_DB)
    params = activity.load_step_params()
    sessions = activity.trainable(activity.read_sessions(conn))
    if not sessions:
        conn.close()
        return {"error": "Library is empty — add some labeled sessions first."}

    learned = []
    X, y, groups = activity.build_examples(
        conn, sessions, params,
        on_session=lambda s, n: learned.append(
            {"id": s.id, "label": s.label, "bucket": s.bucket, "slices": n}))
    conn.close()

    clf, present, report = activity.fit_and_report(X, y, groups)
    joblib.dump({"model": clf, "buckets": present,
                 "features": activity.FEATURE_NAMES, "step_params": params},
                activity.CLF_FILE)
    report["buckets"] = present
    report["learned_from"] = learned
    report["model_path"] = str(activity.CLF_FILE)
    return report
