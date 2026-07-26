#!/usr/bin/env python3
"""Trim a labeled session down to the part that actually matches its label.

Long sprint/run recordings often bleed into walking once you slow down and
stop — that tail poisons the bucket (a 130s "sprint" that's mostly walking
teaches the guesser that walking IS sprinting). This tool lets you cut a session
in the LIBRARY down to just the window you want to keep.

Workflow:

  1. LOOK    python trim.py 29
             Prints a second-by-second intensity bar for the session so you can
             see where the vigorous part ends and the walk-off begins.

  2. CUT     python trim.py 29 --keep 0 18
             Keeps seconds [0, 18) of session 29 and deletes the rest from the
             library. Backs the whole DB up to dumps/ first, so it's reversible.

By default this edits the LIBRARY (labeled_data/labeled_walks.db) — the DB the
guesser trains on. After trimming, retrain:  python activity.py train
"""

import argparse
import math
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

import steps  # shared signal math (step_signal, load_session_acc, SAMPLE_RATE_HZ)

DATA_DIR   = Path(__file__).parent
LIBRARY_DB = DATA_DIR / "labeled_data" / "labeled_walks.db"
DUMP_DIR   = DATA_DIR / "dumps"
RATE       = steps.SAMPLE_RATE_HZ            # 25 Hz


def session_label(conn, sid) -> str | None:
    row = conn.execute("SELECT label FROM sessions WHERE id=?", (sid,)).fetchone()
    if row is None:
        sys.exit(f"No session with id {sid} in this database.")
    return row[0]


def ordered_ids(conn, sid) -> list[int]:
    """acc row ids for a session, in arrival order (the sample order training uses)."""
    return [r[0] for r in conn.execute(
        "SELECT id FROM acc WHERE session=? ORDER BY id", (sid,)).fetchall()]


def show(conn, sid):
    """Per-second intensity bar so you can eyeball where the vigorous part ends."""
    label = session_label(conn, sid)
    samples = steps.load_session_acc(conn, sid)
    n = len(samples)
    if n == 0:
        sys.exit(f"Session {sid} has no accelerometer data.")
    sig = np.abs(steps.step_signal(samples))     # gravity-free motion, >= 0
    dur = n / RATE
    print(f"Session {sid}  label={label!r}   {n} samples ≈ {dur:.0f}s\n")

    # RMS of the gravity-removed motion per 1-second bin → a rough "how hard am I
    # moving right now" trace. Sprinting is tall; a walk-off tail drops off.
    secs = math.ceil(n / RATE)
    per_sec = []
    for s in range(secs):
        seg = sig[s * RATE:(s + 1) * RATE]
        per_sec.append(float(np.sqrt((seg ** 2).mean())) if len(seg) else 0.0)
    hi = max(per_sec) or 1.0

    print(f"{'sec':>5}  intensity")
    print("-" * 46)
    for s, v in enumerate(per_sec):
        bar = "█" * int(v / hi * 34)
        print(f"{s:>3}-{s+1:<2}  {bar} {v:.0f}")
    print(f"\nPick the window to KEEP, then:  python trim.py {sid} --keep <start> <end>")


def cut(conn, sid, keep_start, keep_end, db_path, assume_yes):
    label = session_label(conn, sid)
    ids = ordered_ids(conn, sid)
    n = len(ids)
    if n == 0:
        sys.exit(f"Session {sid} has no accelerometer data.")
    dur = n / RATE

    i0 = max(0, int(round(keep_start * RATE)))
    i1 = min(n, int(round(keep_end * RATE)))
    if i1 <= i0:
        sys.exit(f"Empty keep-window: {keep_start}-{keep_end}s maps to samples "
                 f"[{i0}:{i1}] of {n}. Nothing would remain.")

    keep_ids   = set(ids[i0:i1])
    delete_ids = [i for i in ids if i not in keep_ids]
    if not delete_ids:
        print(f"Session {sid} already fits within {keep_start}-{keep_end}s "
              f"({dur:.0f}s total). Nothing to trim.")
        return

    print(f"Session {sid}  label={label!r}")
    print(f"  now:  {n} samples  = 0-{dur:.0f}s")
    print(f"  keep: {len(keep_ids)} samples = {i0/RATE:.0f}-{i1/RATE:.0f}s")
    print(f"  drop: {len(delete_ids)} samples")

    if not assume_yes:
        reply = input("\nProceed? A backup is saved first. [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted. Nothing changed.")
            return

    # Back the whole DB up before deleting anything — trims aren't otherwise undoable.
    DUMP_DIR.mkdir(exist_ok=True)
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DUMP_DIR / f"{Path(db_path).stem}_pretrim{sid}_{stamp}.db"
    shutil.copy2(db_path, backup)
    print(f"✓ Backup: {backup}")

    with conn:
        conn.executemany("DELETE FROM acc WHERE id=?", [(i,) for i in delete_ids])
    conn.execute("VACUUM")
    remaining = len(ordered_ids(conn, sid))
    print(f"✓ Trimmed session {sid}: {n} → {remaining} samples "
          f"({remaining/RATE:.0f}s). Retrain:  python activity.py train")


def main():
    p = argparse.ArgumentParser(description="Trim a labeled session to its vigorous window.")
    p.add_argument("session", type=int, help="session id to view or trim")
    p.add_argument("--keep", nargs=2, type=float, metavar=("START", "END"),
                   help="seconds to keep, e.g. --keep 0 18 (omit to just view)")
    p.add_argument("--db", default=str(LIBRARY_DB),
                   help="database to edit (default: the training library)")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        if args.keep is None:
            show(conn, args.session)
        else:
            cut(conn, args.session, args.keep[0], args.keep[1], args.db, args.yes)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
