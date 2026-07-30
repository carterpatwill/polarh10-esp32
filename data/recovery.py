#!/usr/bin/env python3
"""Heart-rate recovery from Polar H10 data — how fast you drop after effort.

    steps.py     "how many steps?"          (a number)
    activity.py  "what activity?"           (walk / jog / run / still)
    recovery.py  "how fast do you recover?" (per effort: HRR, tau, HRV rebound)

The idea in one breath: when you finish a hard effort your HR is near its peak;
then you stand or walk it off and it falls. How fast it falls is one of the best
cardiovascular-fitness signals there is. Fitter body → faster drop.

────────────────────────────────────────────────────────────────────────────────
WHAT COUNTS AS A "RECOVERY EVENT"  (the whole mental model)

We use BOTH signals — they check each other:

    ACC  tells us when the *effort ends*  (motion drops from jog/run → walk/still)
    HR   gives us the *peak* right around that moment, and the fall afterwards

So a recovery event is:
    1. START  — the HR peak that lines up with a jog/run → walk/still motion drop.
                (not just the session's max bpm — HR spikes mid-run too.)
    2. WINDOW — keep going through the cooldown; walking AND standing both count.
    3. END    — the moment motion returns to JOG OR ABOVE ("activity again"),
                or a hard time cap (MAX_RECOVERY_SEC), or the recording ends.

A session can hold MANY events (interval workouts!). We score EACH one on its own,
never collapsing them — that's how you watch recovery slow as fatigue accumulates.

────────────────────────────────────────────────────────────────────────────────
THE METRICS  (see docs/2026-07-25-recovery-metrics.md)

    HRR60 / HRR120   bpm dropped in the first 60 s / 120 s after the peak.
                     The clinical standard. Needs no resting HR. <12 poor, >20 good.
    tau (τ)          time constant of an exponential fit HR(t)=rest+(peak-rest)e^(-t/τ).
                     The single best "recovery speed" number. Smaller = fitter.
    to-baseline      seconds until HR falls within BASELINE_MARGIN of resting HR.
    RMSSD@60         HRV (from rr_ms) ~60 s post-peak. Crushed after effort, it climbs
                     back toward your resting RMSSD as the parasympathetic system
                     reactivates. Measured baseline: resting RMSSD ≈ 59 ms.

Resting HR defaults to the measured baseline (≈53 bpm, Pi session 36). Override with
--resting.  Reuses activity.py's trained walk/jog/run classifier to find the efforts,
so run `python activity.py train` first.

────────────────────────────────────────────────────────────────────────────────
USE IT

    python recovery.py events            # most recent session in the dump
    python recovery.py events 25         # a specific session id
    python recovery.py events 25 --resting 53
    python recovery.py plot 25           # HR curve with events + fitted decay marked
    python recovery.py list              # sessions and how much HR/ACC each has
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

import steps      # signal math + load_session_acc
import activity   # the trained walk/jog/run classifier + still threshold

# ── Where the data lives ─────────────────────────────────────────────────────────
DEFAULT_DB = Path(__file__).parent / "hr_data.db"

# ── Measured baselines (Pi session 36, 2026-07-25 — see the recovery-metrics doc) ─
RESTING_HR_BPM   = 53      # decay floor for tau / to-baseline; override with --resting
RESTING_RMSSD_MS = 59      # HRV ceiling — RMSSD climbs back toward this as you recover

# ── What makes an effort, and the shape of a recovery window ──────────────────────
ACTIVE_BUCKETS   = {"jog", "run"}   # "jog or above" = effort. walk/still = recovery.
MIN_ACTIVE_SEC   = 6.0    # an effort block must last this long (ignore stray windows)
MIN_PEAK_RISE    = 25     # peak must be >= resting + this to count as a real effort
PEAK_LOOKBACK    = 20.0   # search this many secs BEFORE the motion drop for the HR peak
PEAK_LOOKAHEAD   = 15.0   # …and this many AFTER (HR lags the body a little)
MAX_RECOVERY_SEC = 300.0  # hard cap on a recovery window if you just stand around
MIN_RECOVERY_SEC = 20.0   # too short to say anything → skip the event
BASELINE_MARGIN  = 10     # "back to baseline" = within this many bpm of resting

# ── HRV / RR cleaning (rr_ms is a JSON list of beat-to-beat intervals, in ms) ─────
RR_MIN_MS, RR_MAX_MS = 300, 1500   # physiologically plausible (40–200 bpm)
RR_MAX_JUMP          = 0.20        # drop a beat that jumps >20% from the previous one
RMSSD_WIN_SEC        = 30.0        # width of the window we sample RMSSD over, at +60s
RMSSD_MIN_BEATS      = 5           # need at least this many clean beats to trust it

# ── When to trust a τ fit (an exponential is meaningless on a short, flat window) ─
TAU_MIN_DUR   = 45.0    # need at least this much of the decay curve
TAU_MIN_DROP  = 6       # …and a real fall of at least this many bpm inside it
TAU_MIN_R2    = 0.60    # …and the log-linear fit must actually be exponential-ish
TAU_MAX_SEC   = 600     # a τ slower than this isn't "recovery", it's a bad fit

# ── Draw/flag a recovery even when it's too noisy for a clean τ ───────────────────
# A big HR fall right after an effort is unmistakably recovery even if its shape is
# too messy for a trustworthy τ. When τ is rejected but the fall to the recovery low
# is real, plot draws a straight peak→low guide line so the drop is still visible.
GUIDE_MIN_DROP = 12     # bpm fall from peak to the recovery low
GUIDE_MIN_SEC  = 15     # …sustained over at least this long (ignore a brief spike)

# ── HRR is only honest if a real reading sits near the +Ns mark, not across a gap ─
HRR_MAX_GAP_SEC = 10.0  # nearest HR reading to the +60/+120 s mark must be within this


# ════════════════════════════════════════════════════════════════════════════════
# PART 1 — load a session onto ONE shared clock (seconds from session start)
# ════════════════════════════════════════════════════════════════════════════════
# HR and ACC both carry the H10's `t_ms` device clock, so they line up directly.
# We shift everything so the earlier of the two streams starts at t=0.
def load_session(conn, sid):
    """Return (hr_t, bpm, rr_beats, acc_samples, acc_t0) all on a shared-seconds clock.

        hr_t       (M,)  seconds-from-start of each HR reading
        bpm        (M,)  the heart rate at each
        rr_beats   list of (t_sec, rr_ms) — every beat-to-beat interval, timed
        acc_samples(N,3) raw x,y,z for activity.py's signal math
        acc_t0     seconds-from-start of the first ACC sample
    """
    hr_rows = conn.execute(
        "SELECT t_ms, bpm, rr_ms FROM hr WHERE session=? ORDER BY t_ms", (sid,)
    ).fetchall()
    acc_min = conn.execute("SELECT MIN(t_ms) FROM acc WHERE session=?", (sid,)).fetchone()[0]
    if not hr_rows:
        sys.exit(f"Session {sid} has no HR data — nothing to recover.")

    t0 = hr_rows[0][0]
    if acc_min is not None:
        t0 = min(t0, acc_min)

    hr_t = np.array([(r[0] - t0) / 1000.0 for r in hr_rows])
    bpm  = np.array([r[1] for r in hr_rows], dtype=float)

    # Expand each reading's rr_ms JSON list into individual timed beats.
    rr_beats = []
    for t_ms, _, rr_json in hr_rows:
        if not rr_json:
            continue
        try:
            vals = json.loads(rr_json)
        except Exception:
            continue
        for v in (vals if isinstance(vals, list) else [vals]):
            rr_beats.append(((t_ms - t0) / 1000.0, float(v)))

    acc_samples = steps.load_session_acc(conn, sid)
    acc_t0 = 0.0 if acc_min is None else (acc_min - t0) / 1000.0
    return hr_t, bpm, rr_beats, acc_samples, acc_t0


# ════════════════════════════════════════════════════════════════════════════════
# PART 2 — the activity timeline: label each 2 s ACC window walk/jog/run/still
# ════════════════════════════════════════════════════════════════════════════════
def activity_timeline(acc_samples, acc_t0):
    """List of (t_start, t_end, label) per 2 s window, on the shared-seconds clock.

    Reuses activity.py's trained classifier AND its 'still is a threshold, not a
    class' override — a motionless body has almost no motion energy, so we call it
    still before trusting the gait model."""
    bundle = activity._load_guesser()
    params = bundle["step_params"]
    sr = params["sample_rate"]

    feats, starts = [], []
    for start, f in activity.slices(acc_samples, params):
        starts.append(start); feats.append(f)
    if not feats:
        return []
    feats = np.array(feats)
    labels = bundle["model"].predict(feats).astype(object)
    still = feats[:, 1] < activity.STILL_INTENSITY     # intensity feature
    labels[still] = activity.STILL_LABEL

    out = []
    for start, lab in zip(starts, labels):
        w_start = acc_t0 + start / sr
        out.append((w_start, w_start + activity.WINDOW_SEC, str(lab)))
    return out


def effort_blocks(timeline):
    """Maximal runs of 'active' (jog/run) windows, as (t_start, t_end) spans.

    A single stray window is smoothed over first so one misread frame doesn't split
    an effort in two (or invent one). Only blocks lasting >= MIN_ACTIVE_SEC survive."""
    if not timeline:
        return []
    active = [lab in ACTIVE_BUCKETS for _, _, lab in timeline]

    # Smooth lone flips: X 0 X → X X X  and  0 X 0 → 0 0 0.
    for i in range(1, len(active) - 1):
        if active[i - 1] == active[i + 1] != active[i]:
            active[i] = active[i - 1]

    blocks, i, n = [], 0, len(active)
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            span = (timeline[i][0], timeline[j][1])
            if span[1] - span[0] >= MIN_ACTIVE_SEC:
                blocks.append(span)
            i = j + 1
        else:
            i += 1
    return blocks


# ════════════════════════════════════════════════════════════════════════════════
# PART 3 — the metrics for one recovery window
# ════════════════════════════════════════════════════════════════════════════════
def hr_at(hr_t, bpm, t):
    """HR at an arbitrary time, interpolated — but only if a real reading is nearby.

    Returns None past the ends of the recording OR when the closest reading is more
    than HRR_MAX_GAP_SEC away, so we never invent a value across a data gap."""
    if t < hr_t[0] or t > hr_t[-1]:
        return None
    if np.min(np.abs(hr_t - t)) > HRR_MAX_GAP_SEC:
        return None
    return float(np.interp(t, hr_t, bpm))


def fit_tau(t_rel, hr, resting):
    """Time constant τ of HR(t)=resting+(peak-resting)·e^(−t/τ), by a log-linear fit.

    An exponential only means something on a window that is long enough, actually
    falls, and genuinely looks exponential — so we gate on duration, real drop, and
    the fit's R². Returns None (shown as '—') whenever any of those fail, rather than
    a confident-looking but meaningless number."""
    if t_rel[-1] - t_rel[0] < TAU_MIN_DUR:
        return None
    if hr[0] - hr.min() < TAU_MIN_DROP:
        return None
    above = hr - resting > 1.0          # ln needs HR above the floor
    if above.sum() < 4:
        return None
    tt, yy = t_rel[above], np.log(hr[above] - resting)
    slope, intercept = np.polyfit(tt, yy, 1)
    if slope >= 0:                      # not decaying toward the floor
        return None
    resid = yy - (slope * tt + intercept)
    ss_tot = np.sum((yy - yy.mean()) ** 2)
    r2 = 1 - np.sum(resid ** 2) / ss_tot if ss_tot > 0 else 0
    tau = -1.0 / slope
    if r2 < TAU_MIN_R2 or tau > TAU_MAX_SEC:
        return None
    return float(tau)


def rmssd(rr_ms):
    """RMSSD (ms) of a cleaned beat-to-beat series, or None if too few beats."""
    if len(rr_ms) < RMSSD_MIN_BEATS:
        return None
    diffs = np.diff(rr_ms)
    return float(np.sqrt(np.mean(diffs * diffs)))


def clean_rr(beats):
    """Drop implausible intervals and >20%-jump artifact beats. beats: [(t, rr)]."""
    kept = [(t, v) for t, v in beats if RR_MIN_MS <= v <= RR_MAX_MS]
    if not kept:
        return []
    good = [kept[0]]
    for t, v in kept[1:]:
        if abs(v - good[-1][1]) / good[-1][1] <= RR_MAX_JUMP:
            good.append((t, v))
    return good


def score_event(hr_t, bpm, rr_beats, peak_t, peak_bpm, end_t, resting):
    """All recovery metrics for one window [peak_t, end_t]. Returns a dict."""
    dur = end_t - peak_t

    # HRR: how far HR fell by +60 s / +120 s (only if the window is that long).
    def drop(sec):
        v = hr_at(hr_t, bpm, peak_t + sec)
        return None if v is None or peak_t + sec > end_t else peak_bpm - v
    hrr60, hrr120 = drop(60), drop(120)

    # τ and time-to-baseline over the readings inside the window.
    inside = (hr_t >= peak_t) & (hr_t <= end_t)
    t_rel, hr_win = hr_t[inside] - peak_t, bpm[inside]
    # Recovery is the FALL from the peak to the lowest HR in the window; a window can
    # run into the next effort ramping back up, and that tail ruins the fit. Fit τ
    # over the descent only, and remember the low for the plotted guide line.
    min_i = int(np.argmin(hr_win))
    desc_t, desc_hr = t_rel[:min_i + 1], hr_win[:min_i + 1]
    tau = fit_tau(desc_t, desc_hr, resting)
    low_t, low_bpm = float(desc_t[-1]), float(desc_hr[-1])

    to_base, reached = None, False
    for tr, h in zip(t_rel, hr_win):
        if h <= resting + BASELINE_MARGIN:
            to_base, reached = float(tr), True
            break

    # HRV rebound: RMSSD over a 30 s window centred on +60 s post-peak.
    lo, hi = peak_t + 60 - RMSSD_WIN_SEC / 2, peak_t + 60 + RMSSD_WIN_SEC / 2
    win_rr = [v for t, v in clean_rr(rr_beats) if lo <= t <= hi] if hi <= end_t + 5 else []
    rmssd60 = rmssd(win_rr)

    return {
        "peak_t": peak_t, "peak_bpm": peak_bpm, "dur": dur,
        "hrr60": hrr60, "hrr120": hrr120, "tau": tau,
        "to_base": to_base, "reached": reached, "rmssd60": rmssd60,
        "end_t": end_t, "low_t": low_t, "low_bpm": low_bpm,
    }


# ════════════════════════════════════════════════════════════════════════════════
# PART 4 — find every recovery event in a session
# ════════════════════════════════════════════════════════════════════════════════
def find_events(hr_t, bpm, rr_beats, timeline, resting):
    """One event per effort: peak matched to the motion drop, window to next effort."""
    blocks = effort_blocks(timeline)
    events = []
    for k, (b_start, b_end) in enumerate(blocks):
        # The peak: highest HR near the moment motion dropped off (end of the effort).
        lo, hi = b_end - PEAK_LOOKBACK, b_end + PEAK_LOOKAHEAD
        near = (hr_t >= lo) & (hr_t <= hi)
        if not near.any():
            continue
        idx = np.argmax(np.where(near, bpm, -np.inf))
        peak_t, peak_bpm = float(hr_t[idx]), float(bpm[idx])
        if peak_bpm < resting + MIN_PEAK_RISE:      # not a real effort → skip
            continue

        # The window ends at the NEXT effort ("activity again"), the cap, or the end.
        next_active = blocks[k + 1][0] if k + 1 < len(blocks) else np.inf
        end_t = min(peak_t + MAX_RECOVERY_SEC, next_active, float(hr_t[-1]))
        if end_t - peak_t < MIN_RECOVERY_SEC:
            continue
        events.append(score_event(hr_t, bpm, rr_beats, peak_t, peak_bpm, end_t, resting))
    return events


# ════════════════════════════════════════════════════════════════════════════════
# PART 4.5 — persist per-event metrics so recovery can be TRENDED over time
# ════════════════════════════════════════════════════════════════════════════════
# A single session's recovery numbers are computed fresh and thrown away. To answer
# "am I getting fitter" we save one row per recovery EVENT (interval workouts have
# several) into recovery_metrics, then compare like-for-like across sessions later.
#
# We store the RAW numbers only — no effort buckets, no HRmax policy — so how we
# group "similar efforts" for the trend can change without a re-backfill.
import datetime as _dt

RECOVERY_TABLE = "recovery_metrics"

# Columns we persist from a score_event() dict, in insert order.
_METRIC_COLS = ("peak_t", "peak_bpm", "dur", "hrr60", "hrr120",
                "tau", "to_base", "reached", "rmssd60")


def ensure_recovery_table(conn):
    """Create recovery_metrics if it isn't there. One row per (session, event)."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {RECOVERY_TABLE} (
            session      INTEGER NOT NULL,
            event_idx    INTEGER NOT NULL,   -- 1-based, matches the `events` display
            peak_t       REAL,               -- secs into the session that HR peaked
            peak_bpm     REAL,               -- the peak (how hard — for matched-effort compare)
            dur          REAL,               -- recovery-window length, secs
            hrr60        REAL,               -- bpm dropped in first 60 s  (the headline)
            hrr120       REAL,               -- bpm dropped in first 120 s
            tau          REAL,               -- decay time-constant, secs (smaller = fitter)
            to_base      REAL,               -- secs back to near-resting (NULL if never)
            reached      INTEGER,            -- 1 if it got back to baseline in the window
            rmssd60      REAL,               -- HRV rebound ~60 s post-peak, ms
            resting_used INTEGER,            -- resting-HR floor these numbers assumed
            computed_at  TEXT,              -- ISO time this row was (re)computed
            PRIMARY KEY (session, event_idx)
        )""")
    conn.commit()


def upsert_recovery(conn, sid, resting=RESTING_HR_BPM):
    """(Re)compute every recovery event for one session and save it. Idempotent:
    replaces the session's existing rows so re-running never duplicates and a session
    that now finds fewer events doesn't leave stale ones behind. Returns the events."""
    ensure_recovery_table(conn)
    hr_t, bpm, rr_beats, acc, acc_t0 = load_session(conn, sid)
    timeline = activity_timeline(acc, acc_t0)
    events = find_events(hr_t, bpm, rr_beats, timeline, resting)

    now = _dt.datetime.now().isoformat(timespec="seconds")
    conn.execute(f"DELETE FROM {RECOVERY_TABLE} WHERE session=?", (sid,))
    for i, e in enumerate(events, 1):
        vals = [e[c] for c in _METRIC_COLS]
        vals = [int(v) if isinstance(v, bool) else v for v in vals]   # reached → 0/1
        conn.execute(
            f"INSERT INTO {RECOVERY_TABLE} "
            f"(session, event_idx, {', '.join(_METRIC_COLS)}, resting_used, computed_at) "
            f"VALUES ({', '.join('?' * (len(_METRIC_COLS) + 4))})",
            [sid, i, *vals, resting, now])
    conn.commit()
    return events


# How we group "similar efforts" for a fair trend. Boundaries are on the PEAK HR the
# recovery started from — comparing a max-effort drop to an easy-jog drop is apples to
# oranges. Kept as read-time policy (not stored) so it's easy to tune.
EFFORT_BUCKETS = ((0, 150, "moderate"), (150, 170, "hard"), (170, 999, "max"))


def effort_bucket(peak_bpm):
    for lo, hi, name in EFFORT_BUCKETS:
        if lo <= peak_bpm < hi:
            return name
    return "?"


# ════════════════════════════════════════════════════════════════════════════════
# PART 5 — commands
# ════════════════════════════════════════════════════════════════════════════════
def _pick_session(conn, session_arg):
    if session_arg is not None:
        if conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_arg,)).fetchone() is None:
            sys.exit(f"No session with id {session_arg}.")
        return session_arg
    row = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        sys.exit("No sessions recorded.")
    return row[0]


def _mmss(sec):
    return f"{int(sec) // 60}:{int(sec) % 60:02d}"


def cmd_list(conn, _args):
    rows = conn.execute(
        """SELECT s.id, s.label,
                  (SELECT COUNT(*) FROM hr  WHERE session=s.id),
                  (SELECT COUNT(*) FROM acc WHERE session=s.id)
             FROM sessions s ORDER BY s.id"""
    ).fetchall()
    if not rows:
        print("No sessions recorded yet."); return
    print(f"{'id':>3}  {'label':<22} {'hr':>6} {'acc':>7}")
    print("-" * 44)
    for sid, label, hr_n, acc_n in rows:
        print(f"{sid:>3}  {(label or ''):<22} {hr_n:>6} {acc_n:>7}")
    print("\nRecovery needs both HR and ACC. Try:  python recovery.py events <id>")


def cmd_events(conn, args):
    sid = _pick_session(conn, args.session)
    label = conn.execute("SELECT label FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    resting = args.resting

    hr_t, bpm, rr_beats, acc, acc_t0 = load_session(conn, sid)
    timeline = activity_timeline(acc, acc_t0)
    events = find_events(hr_t, bpm, rr_beats, timeline, resting)

    head = f"Session {sid}" + (f"  ({label!r})" if label else "")
    print(head)
    print(f"resting HR {resting} bpm   ·   resting RMSSD ~{RESTING_RMSSD_MS} ms   "
          f"·   {_mmss(hr_t[-1])} of HR\n")
    if not events:
        print("No recovery events found.")
        print("(Need a jog/run effort peaking >= "
              f"{resting + MIN_PEAK_RISE} bpm, then a walk/still cooldown.)")
        return

    def s(v, w=6, f="{:.0f}"):
        return (f.format(v) if v is not None else "—").rjust(w)

    print(f"  #   peak    @time    HRR60   HRR120    τ(s)   to-base   RMSSD@60")
    print("-" * 66)
    for i, e in enumerate(events, 1):
        base = (_mmss(e["to_base"]) if e["reached"]
                else f">{_mmss(e['dur'])}").rjust(8)
        print(f"  {i}  {e['peak_bpm']:>4.0f}   {_mmss(e['peak_t']):>6}  "
              f"{s(e['hrr60'])}  {s(e['hrr120'],7)}  {s(e['tau'],6)}  {base}   "
              f"{s(e['rmssd60'],6,'{:.0f}')}"
              + (" ms" if e["rmssd60"] is not None else "   "))

    # A tiny bit of read-it-for-me: best event + a fatigue hint across the session.
    best = max(events, key=lambda e: (e["hrr60"] is not None, e["hrr60"] or -1))
    if best["hrr60"] is not None:
        print(f"\n→ Best recovery: event #{events.index(best)+1}  "
              f"(HRR60 {best['hrr60']:.0f} bpm"
              + (f", τ {best['tau']:.0f}s" if best["tau"] else "") + ")")
    hrrs = [(i, e["hrr60"]) for i, e in enumerate(events, 1) if e["hrr60"] is not None]
    if len(hrrs) >= 2 and hrrs[-1][1] < hrrs[0][1] - 3:
        print("→ Recovery slowing across the session (fatigue accumulating).")


METRIC_KIND = "metric"   # real workouts; the walk/jog/run labeling clips are 'train'


def cmd_backfill(conn, args):
    """Compute + save recovery metrics for REAL workouts, so recovery can be trended.
    By default only sessions tagged kind='metric' count — the walk/jog/run labeling
    clips ('train') are short and would pollute the trend. Pass --all to include them.
    Idempotent; run once to seed, then after each new recording (or just re-run it)."""
    ensure_recovery_table(conn)
    rows = conn.execute(
        """SELECT s.id, s.label, COALESCE(s.kind,''),
                  (SELECT COUNT(*) FROM hr  WHERE session=s.id),
                  (SELECT COUNT(*) FROM acc WHERE session=s.id)
             FROM sessions s ORDER BY s.id"""
    ).fetchall()
    if args.session is not None:
        rows = [r for r in rows if r[0] == args.session]
        if not rows:
            sys.exit(f"No session with id {args.session}.")
    elif not args.all:
        rows = [r for r in rows if r[2] == METRIC_KIND]

    scope = "all sessions" if args.all else f"kind={METRIC_KIND!r} (real workouts)"
    print(f"Backfilling recovery metrics for {scope}.\n")
    total_ev = done = skipped = 0
    kept = []
    print(f"{'id':>3}  {'kind':<7} {'label':<22} {'events':>7}")
    print("-" * 44)
    for sid, label, kind, hr_n, acc_n in rows:
        if not hr_n or not acc_n:
            print(f"{sid:>3}  {kind:<7} {(label or ''):<22} {'— no HR/ACC':>11}")
            skipped += 1
            continue
        try:
            events = upsert_recovery(conn, sid, args.resting)
        except SystemExit as e:            # load_session bails on empty sessions
            print(f"{sid:>3}  {kind:<7} {(label or ''):<22} {'— ' + str(e):>11}")
            skipped += 1
            continue
        total_ev += len(events); done += 1; kept.append(sid)
        print(f"{sid:>3}  {kind:<7} {(label or ''):<22} {len(events):>7}")

    # Keep the table in sync with the filter: purge rows for sessions no longer in
    # scope (e.g. training clips saved by an earlier unfiltered run). A single-session
    # backfill only touches that session, so leave everything else alone.
    purged = 0
    if args.session is None:
        cur = conn.execute(
            f"DELETE FROM {RECOVERY_TABLE} "
            f"WHERE session NOT IN ({','.join('?' * len(kept)) or 'NULL'})", kept)
        purged = cur.rowcount
        conn.commit()

    print("-" * 44)
    print(f"Saved {total_ev} recovery events across {done} sessions "
          f"({skipped} skipped"
          + (f", purged {purged} out-of-scope rows" if purged else "") + ").")
    print("Now:  python recovery.py trend")


def cmd_trend(conn, args):
    """Show saved events grouped by matched effort, oldest→newest, so you can see
    whether recovery is improving. Reads recovery_metrics (run backfill first)."""
    ensure_recovery_table(conn)
    rows = conn.execute(
        f"""SELECT r.session, s.started, s.label, r.event_idx,
                   r.peak_bpm, r.hrr60, r.hrr120, r.tau
              FROM {RECOVERY_TABLE} r JOIN sessions s ON s.id = r.session
             ORDER BY s.started, r.session, r.event_idx"""
    ).fetchall()
    if not rows:
        print("No saved recovery metrics yet. Run:  python recovery.py backfill")
        return

    def s(v, w=6, f="{:.0f}"):
        return (f.format(v) if v is not None else "—").rjust(w)

    buckets = {name: [] for _, _, name in EFFORT_BUCKETS}
    for r in rows:
        buckets.setdefault(effort_bucket(r[4]), []).append(r)

    for _, _, name in EFFORT_BUCKETS:
        evs = buckets.get(name) or []
        lo = next(b[0] for b in EFFORT_BUCKETS if b[2] == name)
        hi = next(b[1] for b in EFFORT_BUCKETS if b[2] == name)
        print(f"\n■ {name.upper()} effort  (peak {lo}–{hi} bpm)   {len(evs)} event(s)")
        if not evs:
            print("   (none yet)"); continue
        print(f"   {'date':<11} {'sess':>4} {'#':>2} {'peak':>5} "
              f"{'HRR60':>6} {'HRR120':>7} {'τ(s)':>6}")
        for sess, started, label, idx, peak, hrr60, hrr120, tau in evs:
            date = (started or "")[:10]
            print(f"   {date:<11} {sess:>4} {idx:>2} {peak:>5.0f} "
                  f"{s(hrr60)} {s(hrr120,7)} {s(tau,6)}")
        # Cheap first↔last read so the trend is obvious even in the CLI.
        h = [e[5] for e in evs if e[5] is not None]
        t = [e[7] for e in evs if e[7] is not None]
        if len(h) >= 2:
            d = h[-1] - h[0]
            print(f"   → HRR60 {h[0]:.0f}→{h[-1]:.0f} "
                  f"({'+' if d >= 0 else ''}{d:.0f}, "
                  f"{'faster recovery' if d > 0 else 'slower' if d < 0 else 'flat'})")
        if len(t) >= 2:
            d = t[-1] - t[0]
            print(f"   → τ    {t[0]:.0f}→{t[-1]:.0f}s "
                  f"({'+' if d >= 0 else ''}{d:.0f}s, "
                  f"{'fitter' if d < 0 else 'slower' if d > 0 else 'flat'})")
    print("\nMatched effort: compare within a bucket, not across. "
          "Higher HRR60 / lower τ over time = getting fitter.")


def cmd_plot(conn, args):
    import matplotlib.pyplot as plt

    sid = _pick_session(conn, args.session)
    label = conn.execute("SELECT label FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    resting = args.resting
    hr_t, bpm, rr_beats, acc, acc_t0 = load_session(conn, sid)
    timeline = activity_timeline(acc, acc_t0)
    events = find_events(hr_t, bpm, rr_beats, timeline, resting)

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(hr_t, bpm, lw=1.2, color="#58a6ff", label="heart rate")
    ax.axhline(resting, color="#8b949e", ls="--", lw=1, label=f"resting {resting}")

    for i, e in enumerate(events, 1):
        ax.axvspan(e["peak_t"], e["end_t"], color="#f85149", alpha=0.10)
        ax.plot(e["peak_t"], e["peak_bpm"], "o", ms=7, color="#f85149")
        ax.annotate(f"#{i}", (e["peak_t"], e["peak_bpm"]),
                    textcoords="offset points", xytext=(4, 6), color="white")
        if e["tau"]:                    # fitted exponential decay, over the descent
            tt = np.linspace(0, e["low_t"], 60)
            ax.plot(e["peak_t"] + tt,
                    resting + (e["peak_bpm"] - resting) * np.exp(-tt / e["tau"]),
                    lw=1.4, color="#f0b429", alpha=0.9,
                    label="fitted decay" if i == 1 else None)
        elif e["low_t"] >= GUIDE_MIN_SEC and e["peak_bpm"] - e["low_bpm"] >= GUIDE_MIN_DROP:
            ax.plot([e["peak_t"], e["peak_t"] + e["low_t"]],  # straight peak→low guide
                    [e["peak_bpm"], e["low_bpm"]],
                    lw=1.4, ls="--", color="#f0b429", alpha=0.9,
                    label="recovery (guide)" if i == 1 else None)

    ax.set_xlabel("seconds"); ax.set_ylabel("bpm")
    ax.set_title(f"Session {sid} — {len(events)} recovery event(s)"
                 + (f"  ({label})" if label else ""))
    ax.legend(loc="upper right")
    ax.set_facecolor("#0d1117"); fig.patch.set_facecolor("#0d1117")
    ax.grid(True, alpha=0.2, ls="--"); ax.tick_params(colors="white")
    for lbl in (ax.xaxis.label, ax.yaxis.label, ax.title):
        lbl.set_color("white")
    for sp in ("bottom", "left"):
        ax.spines[sp].set_color("#30363d")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    plt.tight_layout()
    plt.show()


# ════════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="Heart-rate recovery from Polar H10 data.")
    p.add_argument("--db", default=str(DEFAULT_DB), help="path to a database (default: the dump)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="sessions and how much HR/ACC each has")
    for name, help_ in (("events", "score every recovery event in a session"),
                        ("plot", "draw the HR curve with events and fitted decay")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("session", nargs="?", type=int, help="session id (default: most recent)")
        c.add_argument("--resting", type=int, default=RESTING_HR_BPM,
                       help=f"resting HR floor in bpm (default: {RESTING_HR_BPM})")

    bf = sub.add_parser("backfill",
                        help="compute + SAVE recovery events for real workouts")
    bf.add_argument("session", nargs="?", type=int,
                    help="one session (default: all real workouts)")
    bf.add_argument("--all", action="store_true",
                    help="include training clips too (default: kind='metric' only)")
    bf.add_argument("--resting", type=int, default=RESTING_HR_BPM,
                    help=f"resting HR floor in bpm (default: {RESTING_HR_BPM})")

    sub.add_parser("trend", help="saved events grouped by effort, oldest→newest")

    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        {"list": cmd_list, "events": cmd_events, "plot": cmd_plot,
         "backfill": cmd_backfill, "trend": cmd_trend}[args.cmd](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
