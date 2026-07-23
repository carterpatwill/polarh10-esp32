# Analyze one recorded session (heart rate + accelerometer).
#
# To pull latest data from the Pi:
#   scp carter@pi4server.local:~/projects/python/esp-polar/server/hr_data.db .
#
# Usage:
#   python analyze.py              # most recent session
#   python analyze.py 3            # session id 3
#   python analyze.py --list       # list all sessions and exit
#   python analyze.py --all        # every reading, ignoring sessions (old behavior)

import sqlite3
import json
import sys
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

DB = "hr_data.db"


def avg_rr(val):
    try:
        rr = json.loads(val)
        return sum(rr) / len(rr) if rr else None
    except Exception:
        return None


def list_sessions(conn):
    rows = conn.execute(
        "SELECT id, started, ended, label FROM sessions ORDER BY id"
    ).fetchall()
    if not rows:
        print("No sessions recorded yet.")
        return
    print(f"{'id':>3}  {'started':<19}  {'ended':<19}  label")
    print("-" * 70)
    for sid, started, ended, label in rows:
        print(f"{sid:>3}  {started:<19}  {(ended or '(open)'):<19}  {label or ''}")


def pick_session(conn, arg):
    """Return (session_row_dict_or_None, where_clause, params)."""
    if arg == "--all":
        return None, "1=1", ()
    if arg is not None:                       # explicit id
        row = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (arg,)
        ).fetchone()
        if row is None:
            sys.exit(f"No session with id {arg}. Try --list.")
    else:                                     # most recent
        row = conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            sys.exit("No sessions recorded. Use --all to analyze all readings.")
    return dict(row), "session=?", (row["id"],)


def main():
    arg = None
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    if len(sys.argv) > 1:
        if sys.argv[1] == "--list":
            list_sessions(conn)
            return
        elif sys.argv[1] == "--all":
            arg = "--all"
        else:
            arg = int(sys.argv[1])

    sess, where, params = pick_session(conn, arg)

    hr = pd.read_sql(
        f"SELECT * FROM hr WHERE {where} ORDER BY received", conn, params=params
    )
    acc = pd.read_sql(
        f"SELECT * FROM acc WHERE {where} ORDER BY received", conn, params=params
    )
    conn.close()

    if hr.empty:
        sys.exit("No heart-rate readings for that selection.")

    hr["received"] = pd.to_datetime(hr["received"])
    hr["avg_rr_ms"] = pd.to_numeric(hr["rr_ms"].apply(avg_rr))

    if sess:
        title = f"Session #{sess['id']}  “{sess.get('label') or 'no label'}”"
        duration = (
            pd.to_datetime(sess["ended"]) - pd.to_datetime(sess["started"])
            if sess["ended"] else hr["received"].max() - hr["received"].min()
        )
    else:
        title = "All readings"
        duration = hr["received"].max() - hr["received"].min()

    print("=" * 52)
    print(f"  {title}")
    print("=" * 52)
    if sess:
        print(f"Started   : {sess['started']}")
        print(f"Ended     : {sess['ended'] or '(still open)'}")
    print(f"Duration  : {str(duration).split('.')[0]}")
    print(f"HR reads  : {len(hr)}      ACC samples: {len(acc)}")
    print(f"BPM       — min {hr['bpm'].min()}  max {hr['bpm'].max()}  "
          f"avg {hr['bpm'].mean():.1f}  std {hr['bpm'].std():.1f}")
    if hr["avg_rr_ms"].notna().any():
        rr = hr["avg_rr_ms"].dropna()
        print(f"RR (avg)  — min {rr.min():.0f}ms  max {rr.max():.0f}ms  avg {rr.mean():.0f}ms")
    if acc.empty:
        print("ACC       — no accelerometer samples in this session")

    # ── Plot: HR on top, ACC below (only if we have ACC) ──────────────────────
    have_acc = not acc.empty
    fig, axes = plt.subplots(
        2 if have_acc else 1, 1, figsize=(12, 6 if have_acc else 4),
        sharex=True, squeeze=False,
    )
    ax = axes[0][0]
    ax.plot(hr["received"], hr["bpm"], lw=1.8, color="#58a6ff", solid_capstyle="round")
    ax.axhline(hr["bpm"].mean(), color="#f85149", ls="--", lw=1,
               label=f"avg {hr['bpm'].mean():.1f} BPM")
    ax.fill_between(hr["received"], hr["bpm"], hr["bpm"].mean(), alpha=0.1, color="#58a6ff")
    ax.set_ylabel("BPM")
    ax.set_title(f"{title} — Heart Rate")
    ax.legend()

    if have_acc:
        acc["received"] = pd.to_datetime(acc["received"])
        ax2 = axes[1][0]
        for col, color in (("x", "#58a6ff"), ("y", "#3fb950"), ("z", "#d29922")):
            ax2.plot(acc["received"], acc[col], lw=0.8, color=color, label=col, alpha=0.9)
        ax2.set_ylabel("mg")
        ax2.set_title("Accelerometer")
        ax2.legend(ncol=3)

    # dark theme
    for row in axes:
        a = row[0]
        a.set_facecolor("#0d1117")
        a.grid(True, alpha=0.2, ls="--")
        a.tick_params(colors="white")
        a.yaxis.label.set_color("white")
        a.title.set_color("white")
        for s in ("bottom", "left"):
            a.spines[s].set_color("#30363d")
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    axes[-1][0].xaxis.set_major_formatter(mdates.DateFormatter("%I:%M:%S %p"))
    axes[-1][0].set_xlabel("Time")
    axes[-1][0].xaxis.label.set_color("white")
    fig.patch.set_facecolor("#0d1117")
    fig.autofmt_xdate(rotation=30, ha="right")
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
