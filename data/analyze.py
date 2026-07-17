# To pull latest data from the Pi:
# scp carter@pi4server.local:~/projects/python/esp-polar/hr_receiver/hr_data.db .

import sqlite3
import json
import pandas as pd
import matplotlib.pyplot as plt

DB = "hr_data.db"

conn = sqlite3.connect(DB)
df = pd.read_sql("SELECT * FROM readings ORDER BY received", conn)
conn.close()

df["received"] = pd.to_datetime(df["received"])

def avg_rr(val):
    try:
        rr = json.loads(val)
        return sum(rr) / len(rr) if rr else None
    except Exception:
        return None

df["avg_rr_ms"] = pd.to_numeric(df["rr_ms"].apply(avg_rr))

duration = df["received"].max() - df["received"].min()

print("=== Summary ===")
print(f"Total readings : {len(df)}")
print(f"Duration       : {str(duration).split('.')[0]}")
print(f"BPM  — min: {df['bpm'].min()}  max: {df['bpm'].max()}  avg: {df['bpm'].mean():.1f}  std: {df['bpm'].std():.1f}")
if df["avg_rr_ms"].notna().any():
    print(f"RR   — min: {df['avg_rr_ms'].min():.0f}ms  max: {df['avg_rr_ms'].max():.0f}ms  avg: {df['avg_rr_ms'].mean():.0f}ms")

import matplotlib.dates as mdates

fig, ax = plt.subplots(figsize=(12, 4))
ax.plot(df["received"], df["bpm"], linewidth=1.5, color="#58a6ff", alpha=0.9, solid_capstyle="round", solid_joinstyle="round")
ax.axhline(df["bpm"].mean(), color="#f85149", linestyle="--", linewidth=1, label=f"avg {df['bpm'].mean():.1f} BPM")
ax.fill_between(df["received"], df["bpm"], df["bpm"].mean(), alpha=0.1, color="#58a6ff")
ax.set_title("Heart Rate over Time")
ax.set_xlabel("Time")
ax.set_ylabel("BPM")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%I:%M:%S %p"))
fig.autofmt_xdate(rotation=30, ha="right")
ax.legend()
ax.grid(True, alpha=0.2, linestyle="--")
ax.set_facecolor("#0d1117")
fig.patch.set_facecolor("#0d1117")
ax.tick_params(colors="white")
ax.xaxis.label.set_color("white")
ax.yaxis.label.set_color("white")
ax.title.set_color("white")
ax.spines["bottom"].set_color("#30363d")
ax.spines["left"].set_color("#30363d")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
plt.tight_layout()
plt.show()
