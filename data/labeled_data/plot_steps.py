#!/usr/bin/env python3
"""Plot every labeled walk with its detected steps marked (the red dots).

This lives next to the frozen training data (labeled_walks.db) so you can always
re-draw exactly what the step detector sees on the walks it was calibrated on —
even after dump-pi.sh has overwritten ../hr_data.db with a newer pull.

It reuses the signal processing in ../steps.py, so there's one source of truth
for what "a step" is.

Usage:
  python plot_steps.py                 # save a grid of all walks → steps_plot.png
  python plot_steps.py --show          # also pop up the interactive window
  python plot_steps.py 16              # just session 16, full size, on screen
  python plot_steps.py --db other.db   # plot a different database

Each subplot: blue = acceleration magnitude with gravity removed, dashed line =
the step threshold, red dot = a detected step. One clean crest ≈ one footfall.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

# Reuse the detector from ../steps.py — same math the calibration used.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import steps  # noqa: E402

DEFAULT_DB    = HERE / "labeled_walks.db"
DEFAULT_MODEL = HERE.parent / "steps_model.json"


def load_params(model_path: Path) -> dict:
    if not model_path.exists():
        sys.exit(f"No calibration at {model_path}.\n"
                 f"Run first:  python ../steps.py calibrate")
    return json.loads(model_path.read_text())


def labeled_walks(conn):
    """(id, label, true_count) for sessions with a numeric label AND acc data."""
    out = []
    for sid, label, acc_rows in steps.all_sessions(conn):
        truth = steps.true_count_from_label(label)
        if truth is not None and acc_rows > 0:
            out.append((sid, label, truth))
    return out


def style_axis(ax):
    ax.set_facecolor("#0d1117")
    ax.grid(True, alpha=0.15, ls="--")
    ax.tick_params(colors="#8b949e", labelsize=8)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("bottom", "left"):
        ax.spines[sp].set_color("#30363d")
    ax.margins(x=0.005)


def draw(ax, conn, params, sid, label, truth):
    samples = steps.load_session_acc(conn, sid)
    n, sig, peaks = steps.count_steps(samples, params)
    t = np.arange(len(sig)) / params["sample_rate"]
    height = params["thresh_k"] * sig.std()

    ax.plot(t, sig, lw=0.9, color="#58a6ff")
    ax.axhline(height, color="#8b949e", ls="--", lw=0.8)
    if peaks:
        ax.plot(t[peaks], sig[peaks], "o", ms=4, color="#f85149")

    err = f"{n - truth:+d}" if truth is not None else ""
    truth_s = f" / true {truth}  ({err})" if truth is not None else ""
    ax.set_title(f"#{sid}  {label}  —  counted {n}{truth_s}",
                 color="white", fontsize=10, loc="left")
    style_axis(ax)
    return n


def main():
    p = argparse.ArgumentParser(description="Plot detected steps on labeled walks.")
    p.add_argument("session", nargs="?", type=int, help="one session id (default: all)")
    p.add_argument("--db", default=str(DEFAULT_DB), help="database to read")
    p.add_argument("--model", default=str(DEFAULT_MODEL), help="calibration file")
    p.add_argument("--show", action="store_true", help="also open the interactive window")
    p.add_argument("--out", default=str(HERE / "steps_plot.png"), help="PNG output path")
    args = p.parse_args()

    params = load_params(Path(args.model))
    conn = sqlite3.connect(args.db)

    if args.session is not None:
        row = conn.execute("SELECT label FROM sessions WHERE id=?", (args.session,)).fetchone()
        if row is None:
            sys.exit(f"No session with id {args.session} in {args.db}.")
        walks = [(args.session, row[0] or f"session {args.session}",
                  steps.true_count_from_label(row[0]))]
    else:
        walks = labeled_walks(conn)
        if not walks:
            sys.exit(f"No labeled walks (numeric label + acc data) in {args.db}.")

    fig, axes = plt.subplots(len(walks), 1, figsize=(14, 2.2 * len(walks)), squeeze=False)
    for ax, (sid, label, truth) in zip(axes[:, 0], walks):
        draw(ax, conn, params, sid, label, truth)
    axes[-1, 0].set_xlabel("seconds", color="#8b949e")
    fig.suptitle("Detected steps (red) vs acceleration wave — gravity removed",
                 color="white", y=0.997)
    fig.patch.set_facecolor("#0d1117")
    plt.tight_layout(rect=[0, 0, 1, 0.99])

    plt.savefig(args.out, dpi=110, facecolor="#0d1117")
    print(f"Saved → {args.out}")
    if args.show or args.session is not None:
        plt.show()
    conn.close()


if __name__ == "__main__":
    main()
