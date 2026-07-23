#!/usr/bin/env python3
"""
Plot accelerometer axes (X, Y, Z) and heart rate over time from an hr_data.db.

Usage:
    python plot.py                      # uses data/hr_data.db
    python plot.py path/to/hr_data.db   # any dump
    python plot.py path/to.db out.png   # also save a PNG instead of showing
"""
import sqlite3
import sys
import os
import pandas as pd
import matplotlib.pyplot as plt

# ── Pick the database: 1st arg, else the file next to this script ────────────
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "hr_data.db")
SAVE = sys.argv[2] if len(sys.argv) > 2 else None

if not os.path.exists(DB):
    sys.exit(f"❌ Database not found: {DB}")

conn = sqlite3.connect(DB)
acc = pd.read_sql("SELECT t_ms, x, y, z FROM acc ORDER BY t_ms", conn)
hr = pd.read_sql("SELECT t_ms, bpm FROM hr ORDER BY t_ms", conn)
conn.close()

# ── Shared timeline: seconds since the earliest sample across both tables ─────
t0 = min(
    acc["t_ms"].min() if not acc.empty else float("inf"),
    hr["t_ms"].min() if not hr.empty else float("inf"),
)
if not acc.empty:
    acc["t"] = (acc["t_ms"] - t0) / 1000.0
if not hr.empty:
    hr["t"] = (hr["t_ms"] - t0) / 1000.0

# ── Summary ──────────────────────────────────────────────────────────────────
print("=== Summary ===")
print(f"Database   : {DB}")
print(f"Acc samples: {len(acc)}   HR samples: {len(hr)}")
if not hr.empty:
    print(f"BPM — min: {hr['bpm'].min()}  max: {hr['bpm'].max()}  avg: {hr['bpm'].mean():.1f}")

# ── Dark theme to match analyze.py ───────────────────────────────────────────
BG = "#0d1117"
plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "text.color": "white", "axes.labelcolor": "white", "axes.titlecolor": "white",
    "xtick.color": "white", "ytick.color": "white",
    "axes.edgecolor": "#30363d", "grid.color": "#30363d",
})

fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)

specs = [
    (axes[0], acc, "x",   "X axis (milli-g)", "#f85149"),
    (axes[1], acc, "y",   "Y axis (milli-g)", "#3fb950"),
    (axes[2], acc, "z",   "Z axis (milli-g)", "#58a6ff"),
    (axes[3], hr,  "bpm", "Heart rate (BPM)", "#d29922"),
]

for ax, data, col, label, color in specs:
    if not data.empty:
        ax.plot(data["t"], data[col], linewidth=1.2, color=color,
                solid_capstyle="round", solid_joinstyle="round")
    ax.set_ylabel(label)
    ax.grid(True, alpha=0.25, linestyle="--")
    ax.margins(x=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

axes[3].set_xlabel("Time (seconds since start)")
fig.suptitle(f"Accelerometer & Heart Rate — {os.path.basename(DB)}", color="white")
plt.tight_layout()

if SAVE:
    fig.savefig(SAVE, dpi=120)
    print(f"→ Saved {SAVE}")
else:
    plt.show()
