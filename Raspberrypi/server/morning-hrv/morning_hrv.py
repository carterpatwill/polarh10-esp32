#!/usr/bin/env python3
"""Hands-off morning HRV baseline capture, straight from the Pi's own Bluetooth.

The idea: the Pi lives in your bedroom. Some time in the morning you put on the
Polar H10. The Pi is watching for it (during a configurable window, default
07:00–11:00), and the moment the strap appears it connects, records a few quiet
minutes of beat-to-beat (RR) intervals, computes your morning HRV — RMSSD /
lnRMSSD plus resting HR and an estimated respiration rate — and files one row per
morning into hr_data.db. All you did was put the strap on.

Why RR from the standard HR service (and not the PMD/ACC link): resting HRV only
needs the beat-to-beat intervals, which the H10 broadcasts on the plain, un-
encrypted Heart Rate Measurement characteristic (0x2A37). No secure PMD channel,
no accelerometer, none of the ACC-on-XIAO pairing saga — just subscribe and read.

This is the PRODUCER (writes to hr_data.db), a sibling to server.py. The dashboard
(api/morning.py) is the CONSUMER that turns these rows into a rolling readiness
baseline. Both run on the Pi; this file only needs `bleak` + `numpy`.

Commands:
    watch      run forever; capture once per morning inside the window  (the service)
    once       capture right now (ignores the window) — for testing
    backfill   compute a morning row from an already-recorded still session
    list       print the stored morning readings
    baseline   print the rolling lnRMSSD baseline + today's status

    python3 morning_hrv.py once --duration 240
    python3 morning_hrv.py backfill 36
    python3 morning_hrv.py list
"""
import argparse
import asyncio
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# ── Bluetooth ────────────────────────────────────────────────────────────────
# Standard BLE Heart Rate Measurement characteristic — carries HR + RR intervals.
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
DEVICE_NAME_PREFIX  = "Polar H10"   # the H10 advertises as "Polar H10 XXXXXXXX"
SCAN_TIMEOUT_S      = 12.0          # how long each scan attempt looks for the strap

# ── Capture window / timing ──────────────────────────────────────────────────
WINDOW_START_HOUR = 7              # only auto-capture between these hours (local)
WINDOW_END_HOUR   = 11
CAPTURE_SECONDS   = 240            # ~4 min of recording once connected
WARMUP_SECONDS    = 60            # discard the first minute (settling in, adjusting strap)
POLL_SECONDS      = 30            # how often `watch` re-scans while waiting for the strap

# ── RR cleaning (mirrors data/recovery.py so the numbers agree) ──────────────
RR_MIN_MS   = 300.0    # < 300 ms  → 200 bpm, implausible at rest → artifact
RR_MAX_MS   = 1500.0   # > 1500 ms → 40 bpm,  dropped-beat artifact
RR_MAX_JUMP = 0.20     # a >20% jump from the last good beat is an ectopic/motion artifact
MIN_BEATS   = 60       # need at least this many clean beats to trust a morning reading
ARTIFACT_MAX_PCT = 5.0 # above this fraction rejected → too much movement → quality 'low'

# ── Respiration (RR-derived, RSA band) ───────────────────────────────────────
RESP_FS_HZ   = 4.0     # resample the RR tachogram to this even rate before the FFT
RESP_LO_HZ   = 0.15    # 0.15–0.40 Hz  ==  9–24 breaths/min, the plausible resting band
RESP_HI_HZ   = 0.40

# The receiver's DB sits in the server/ parent (shared with workout-daq + the
# dashboard); this file now sits in server/morning-hrv/. HR_DB overrides.
DB_PATH = Path(os.environ.get("HR_DB", Path(__file__).parent.parent / "hr_data.db"))
LABEL   = "morning hrv"          # session label so the raw trace is recognisable in the UI


# ════════════════════════════════════════════════════════════════════════════
# Metrics — everything is computed from the cleaned RR series
# ════════════════════════════════════════════════════════════════════════════
def clean_rr(beats):
    """Drop implausible intervals and >20%-jump artifact beats. beats: [(t, rr_ms)].

    Same two-stage filter as recovery.py: first a hard physiological range, then a
    successive-difference gate that removes ectopic/motion spikes. Returns the kept
    (t, rr) pairs in order."""
    kept = [(t, v) for t, v in beats if RR_MIN_MS <= v <= RR_MAX_MS]
    if not kept:
        return []
    good = [kept[0]]
    for t, v in kept[1:]:
        if abs(v - good[-1][1]) / good[-1][1] <= RR_MAX_JUMP:
            good.append((t, v))
    return good


def _rmssd(rr):
    if len(rr) < 5:
        return None
    d = np.diff(rr)
    return float(np.sqrt(np.mean(d * d)))


def respiration_rate(rr):
    """Breaths per minute, estimated from respiratory sinus arrhythmia in the RR series.

    Breathing modulates the beat-to-beat interval (you speed up on the inhale, slow
    on the exhale). So the dominant frequency of the RR tachogram in the 0.15–0.40 Hz
    band IS the breathing rate. We rebuild an even time axis from the intervals
    themselves (cumulative RR = beat clock), window it, and take the FFT peak in-band.
    numpy-only — no scipy. Returns breaths/min, or None if the series is too short."""
    if len(rr) < 20:
        return None
    t = np.cumsum(np.array(rr, dtype=float)) / 1000.0      # beat times, seconds
    tt = np.arange(t[0], t[-1], 1.0 / RESP_FS_HZ)
    if len(tt) < 32:
        return None
    x = np.interp(tt, t, rr)
    x = x - x.mean()
    x = x * np.hanning(len(x))                              # taper to sharpen the peak
    freqs = np.fft.rfftfreq(len(x), 1.0 / RESP_FS_HZ)
    psd   = np.abs(np.fft.rfft(x)) ** 2
    band  = (freqs >= RESP_LO_HZ) & (freqs <= RESP_HI_HZ)
    if not band.any() or psd[band].max() <= 0:
        return None
    f_peak = freqs[band][int(np.argmax(psd[band]))]
    return round(f_peak * 60.0, 1)


def compute_metrics(beats):
    """Turn raw (t, rr_ms) beats into the morning summary dict.

    `beats` should already have the warm-up seconds trimmed. Returns None only if
    nothing survived cleaning; otherwise always returns a dict (with a 'quality'
    flag) so a fidgety morning is still recorded rather than silently dropped."""
    raw_n = len(beats)
    if raw_n == 0:
        return None
    clean = clean_rr(beats)
    if not clean:
        return None
    rr = [v for _, v in clean]
    rr_arr = np.array(rr, dtype=float)

    rmssd = _rmssd(rr)
    sdnn  = float(np.std(rr_arr, ddof=1)) if len(rr_arr) > 1 else None
    pnn50 = float(np.mean(np.abs(np.diff(rr_arr)) > 50.0) * 100.0) if len(rr_arr) > 1 else None
    hr_rest = round(60000.0 / float(np.median(rr_arr)), 1)   # from RR, not the noisy bpm field
    artifact_pct = round(100.0 * (1.0 - len(clean) / raw_n), 1)

    quality = "good" if (len(clean) >= MIN_BEATS and artifact_pct <= ARTIFACT_MAX_PCT) else "low"

    return {
        "beats":        len(clean),
        "artifact_pct": artifact_pct,
        "hr_rest":      hr_rest,
        "rmssd":        round(rmssd, 1) if rmssd is not None else None,
        "ln_rmssd":     round(math.log(rmssd), 3) if rmssd else None,
        "sdnn":         round(sdnn, 1) if sdnn is not None else None,
        "pnn50":        round(pnn50, 1) if pnn50 is not None else None,
        "resp_rate":    respiration_rate(rr),
        "quality":      quality,
    }


# ════════════════════════════════════════════════════════════════════════════
# Storage — raw trace into hr/sessions (like the ESP path), summary into morning_hrv
# ════════════════════════════════════════════════════════════════════════════
def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS morning_hrv (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT UNIQUE,       -- YYYY-MM-DD, one reading per morning (latest wins)
            captured_at  TEXT,              -- ISO datetime the reading started
            session      INTEGER,           -- link to the raw hr rows / sessions row
            duration_s   INTEGER,
            beats        INTEGER,           -- clean beats used
            artifact_pct REAL,              -- % of beats rejected (a movement proxy)
            hr_rest      REAL,              -- resting HR from median RR
            rmssd        REAL,
            ln_rmssd     REAL,              -- what the rolling baseline is built on
            sdnn         REAL,
            pnn50        REAL,
            resp_rate    REAL,              -- breaths/min, RR-derived (nullable)
            quality      TEXT               -- 'good' | 'low'
        )
    """)
    conn.commit()


def _has_kind(conn):
    return any(r[1] == "kind" for r in conn.execute("PRAGMA table_info(sessions)"))


def store_reading(conn, packets, started_dt, metrics, day=None):
    """Persist one morning: a session + its hr rows, plus the summary row.

    `packets` is [(epoch_ms, bpm, [rr_ms, ...]), ...] as received. The raw trace goes
    into sessions/hr exactly like a recorded workout (kind='metric', label 'morning
    hrv') so it shows up and can be re-analysed; the summary is upserted into
    morning_hrv keyed by date so re-capturing a day overwrites it."""
    ensure_table(conn)
    day = day or started_dt.date().isoformat()
    started_iso = started_dt.isoformat(timespec="seconds")
    received = datetime.now().isoformat(timespec="seconds")

    if _has_kind(conn):
        cur = conn.execute("INSERT INTO sessions (started, ended, label, kind) VALUES (?, ?, ?, 'metric')",
                           (started_iso, received, LABEL))
    else:
        cur = conn.execute("INSERT INTO sessions (started, ended, label) VALUES (?, ?, ?)",
                           (started_iso, received, LABEL))
    sid = cur.lastrowid

    conn.executemany(
        "INSERT INTO hr (received, t_ms, bpm, rr_ms, session) VALUES (?, ?, ?, ?, ?)",
        [(received, int(t_ms), int(bpm),
          json.dumps([round(v) for v in rr]) if rr else None, sid)
         for t_ms, bpm, rr in packets],
    )

    dur = int((packets[-1][0] - packets[0][0]) / 1000) if len(packets) > 1 else 0
    conn.execute("""
        INSERT INTO morning_hrv
            (date, captured_at, session, duration_s, beats, artifact_pct,
             hr_rest, rmssd, ln_rmssd, sdnn, pnn50, resp_rate, quality)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            captured_at=excluded.captured_at, session=excluded.session,
            duration_s=excluded.duration_s, beats=excluded.beats,
            artifact_pct=excluded.artifact_pct, hr_rest=excluded.hr_rest,
            rmssd=excluded.rmssd, ln_rmssd=excluded.ln_rmssd, sdnn=excluded.sdnn,
            pnn50=excluded.pnn50, resp_rate=excluded.resp_rate, quality=excluded.quality
    """, (day, started_iso, sid, dur, metrics["beats"], metrics["artifact_pct"],
          metrics["hr_rest"], metrics["rmssd"], metrics["ln_rmssd"], metrics["sdnn"],
          metrics["pnn50"], metrics["resp_rate"], metrics["quality"]))
    conn.commit()
    return sid, day


# ════════════════════════════════════════════════════════════════════════════
# BLE capture
# ════════════════════════════════════════════════════════════════════════════
def parse_hr_measurement(data):
    """Decode a Heart Rate Measurement notification → (bpm, [rr_ms, ...]).

    Layout per the BLE spec: a flags byte, then HR (8- or 16-bit per flag bit 0),
    an optional energy-expended field (bit 3), then zero or more RR intervals (bit
    4) in units of 1/1024 s — which we convert to milliseconds."""
    flags = data[0]
    idx = 1
    if flags & 0x01:                       # HR is 16-bit
        bpm = int.from_bytes(data[idx:idx + 2], "little"); idx += 2
    else:
        bpm = data[idx]; idx += 1
    if flags & 0x08:                       # energy expended present → skip 2 bytes
        idx += 2
    rr = []
    if flags & 0x10:                       # RR intervals present
        while idx + 2 <= len(data):
            raw = int.from_bytes(data[idx:idx + 2], "little"); idx += 2
            rr.append(raw * 1000.0 / 1024.0)
    return bpm, rr


async def capture(duration=CAPTURE_SECONDS, scan_timeout=SCAN_TIMEOUT_S, verbose=True):
    """Find the strap, record `duration` seconds of HR/RR, return the raw packets.

    Returns (packets, started_dt) or (None, None) if the strap never showed up.
    packets: [(epoch_ms, bpm, [rr_ms, ...]), ...]."""
    from bleak import BleakClient, BleakScanner   # imported lazily so `list`/`baseline` need no BLE

    def _match(dev, _adv):
        return bool(dev.name and dev.name.startswith(DEVICE_NAME_PREFIX))

    if verbose:
        print(f"[scan] looking for '{DEVICE_NAME_PREFIX}…' ({scan_timeout:.0f}s)…")
    device = await BleakScanner.find_device_by_filter(_match, timeout=scan_timeout)
    if device is None:
        return None, None
    if verbose:
        print(f"[scan] found {device.name} ({device.address}) — connecting…")

    packets = []
    started_dt = datetime.now()

    def on_hr(_char, data):
        bpm, rr = parse_hr_measurement(bytes(data))
        packets.append((int(time.time() * 1000), bpm, rr))

    async with BleakClient(device) as client:
        await client.start_notify(HR_MEASUREMENT_UUID, on_hr)
        if verbose:
            print(f"[rec ] recording {duration}s … (first {WARMUP_SECONDS}s discarded as warm-up)")
        # Poll instead of one long sleep so we notice an early disconnect.
        elapsed = 0.0
        while elapsed < duration and client.is_connected:
            await asyncio.sleep(2.0)
            elapsed += 2.0
        try:
            await client.stop_notify(HR_MEASUREMENT_UUID)
        except Exception:
            pass
    if verbose:
        beats = sum(len(rr) for _, _, rr in packets)
        print(f"[rec ] done — {len(packets)} packets, {beats} RR intervals")
    return (packets or None), started_dt


def _warmup_trimmed_beats(packets, warmup=WARMUP_SECONDS):
    """Flatten packets → [(t_sec, rr_ms)], dropping the first `warmup` seconds."""
    if not packets:
        return []
    t0 = packets[0][0]
    beats = []
    for epoch_ms, _bpm, rr in packets:
        t = (epoch_ms - t0) / 1000.0
        if t < warmup:
            continue
        for v in rr:
            beats.append((t, v))
    return beats


# ════════════════════════════════════════════════════════════════════════════
# Rolling baseline (terminal view; the dashboard computes its own for the UI)
# ════════════════════════════════════════════════════════════════════════════
BASELINE_WINDOW_DAYS = 60
BASELINE_MIN_READINGS = 7


def compute_baseline(rows):
    """From morning_hrv rows (each a dict with date + ln_rmssd), return baseline stats.

    Uses the last BASELINE_WINDOW_DAYS of readings: mean lnRMSSD ± 1 SD is your
    'normal range'. Today lands balanced / below (under-recovered) / above. None of
    it means much until BASELINE_MIN_READINGS mornings are in."""
    vals = [(r["date"], r["ln_rmssd"]) for r in rows if r.get("ln_rmssd") is not None]
    vals.sort()
    if not vals:
        return {"ready": False, "n": 0, "need": BASELINE_MIN_READINGS}
    window = vals[-BASELINE_WINDOW_DAYS:]
    lns = np.array([v for _, v in window], dtype=float)
    mean = float(lns.mean())
    sd   = float(lns.std(ddof=1)) if len(lns) > 1 else 0.0
    today_date, today = vals[-1]
    lo, hi = mean - sd, mean + sd
    status = ("balanced" if lo <= today <= hi else
              "low" if today < lo else "elevated")
    return {
        "ready": len(window) >= BASELINE_MIN_READINGS,
        "n": len(window), "need": BASELINE_MIN_READINGS,
        "mean": round(mean, 3), "sd": round(sd, 3),
        "lo": round(lo, 3), "hi": round(hi, 3),
        "today": round(today, 3), "today_date": today_date,
        "status": status,
    }


# ════════════════════════════════════════════════════════════════════════════
# Commands
# ════════════════════════════════════════════════════════════════════════════
def _fetch_rows(conn):
    ensure_table(conn)
    cur = conn.execute("SELECT * FROM morning_hrv ORDER BY date")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _finish_capture(conn, packets, started_dt, day=None):
    """Shared tail for `once`/`watch`: trim, compute, store, print."""
    beats = _warmup_trimmed_beats(packets)
    metrics = compute_metrics(beats)
    if metrics is None:
        print("[fail] no clean RR intervals — strap not seated? Nothing stored.")
        return False
    sid, day = store_reading(conn, packets, started_dt, metrics, day=day)
    q = metrics["quality"]
    flag = "" if q == "good" else "  ⚠ LOW QUALITY (movement / short reading)"
    print(f"[save] {day}  session {sid}  ·  RMSSD {metrics['rmssd']} ms  "
          f"(ln {metrics['ln_rmssd']})  ·  rest HR {metrics['hr_rest']}  ·  "
          f"resp {metrics['resp_rate']}/min  ·  {metrics['beats']} beats, "
          f"{metrics['artifact_pct']}% rejected{flag}")
    return q == "good"


async def cmd_once(conn, args):
    packets, started_dt = await capture(duration=args.duration)
    if packets is None:
        print("[scan] strap not found — is the H10 on and awake?")
        return
    _finish_capture(conn, packets, started_dt)


async def cmd_watch(conn, args):
    """Run forever; capture once per morning inside the window. This is the service."""
    print(f"[watch] morning window {WINDOW_START_HOUR:02d}:00–{WINDOW_END_HOUR:02d}:00, "
          f"capture {args.duration}s, re-scan every {POLL_SECONDS}s")
    done_for = None                                  # date we already captured
    while True:
        now = datetime.now()
        in_window = WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR
        today = now.date().isoformat()
        if done_for != today and now.hour < WINDOW_START_HOUR:
            done_for = None                          # new day rolled over before the window
        if in_window and done_for != today:
            packets, started_dt = await capture(duration=args.duration, verbose=True)
            if packets is not None:
                ok = _finish_capture(conn, packets, started_dt, day=today)
                if ok:
                    done_for = today                 # good reading → leave you alone till tomorrow
                else:
                    print("[watch] low-quality reading — will retry on the next scan")
            else:
                await asyncio.sleep(POLL_SECONDS)    # strap not on yet; keep watching
        else:
            await asyncio.sleep(POLL_SECONDS)


def cmd_backfill(conn, args):
    """Compute a morning row from an already-recorded still session (for testing).

    Reuses the raw hr rows of an existing session — handy to seed the baseline from
    past resting recordings (e.g. the 'resting heart rate' session) without waiting
    for real mornings."""
    rows = conn.execute(
        "SELECT t_ms, bpm, rr_ms FROM hr WHERE session=? ORDER BY t_ms", (args.session,)
    ).fetchall()
    if not rows:
        sys.exit(f"session {args.session} has no hr rows")
    meta = conn.execute("SELECT started FROM sessions WHERE id=?", (args.session,)).fetchone()
    started_dt = datetime.fromisoformat(meta[0]) if meta and meta[0] else datetime.now()

    packets = []
    for t_ms, bpm, rr_json in rows:
        rr = json.loads(rr_json) if rr_json else []
        packets.append((int(t_ms), int(bpm), [float(v) for v in rr]))
    # backfill uses the whole recording (already-quiet sessions have no warm-up to trim)
    beats = _warmup_trimmed_beats(packets, warmup=0)
    metrics = compute_metrics(beats)
    if metrics is None:
        sys.exit("no clean RR in that session")
    day = args.date or started_dt.date().isoformat()
    # Point the summary at the source session rather than duplicating the trace.
    ensure_table(conn)
    dur = int((packets[-1][0] - packets[0][0]) / 1000) if len(packets) > 1 else 0
    conn.execute("""
        INSERT INTO morning_hrv
            (date, captured_at, session, duration_s, beats, artifact_pct,
             hr_rest, rmssd, ln_rmssd, sdnn, pnn50, resp_rate, quality)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            captured_at=excluded.captured_at, session=excluded.session,
            duration_s=excluded.duration_s, beats=excluded.beats,
            artifact_pct=excluded.artifact_pct, hr_rest=excluded.hr_rest,
            rmssd=excluded.rmssd, ln_rmssd=excluded.ln_rmssd, sdnn=excluded.sdnn,
            pnn50=excluded.pnn50, resp_rate=excluded.resp_rate, quality=excluded.quality
    """, (day, started_dt.isoformat(timespec="seconds"), args.session, dur,
          metrics["beats"], metrics["artifact_pct"], metrics["hr_rest"], metrics["rmssd"],
          metrics["ln_rmssd"], metrics["sdnn"], metrics["pnn50"], metrics["resp_rate"],
          metrics["quality"]))
    conn.commit()
    print(f"[backfill] {day} ← session {args.session}: RMSSD {metrics['rmssd']} ms "
          f"(ln {metrics['ln_rmssd']}), rest HR {metrics['hr_rest']}, "
          f"resp {metrics['resp_rate']}/min, {metrics['beats']} beats [{metrics['quality']}]")


def cmd_list(conn, _args):
    rows = _fetch_rows(conn)
    if not rows:
        print("no morning readings yet")
        return
    print(f"{'date':12} {'RMSSD':>6} {'lnRMSSD':>8} {'restHR':>7} {'resp':>6} "
          f"{'SDNN':>6} {'pNN50':>6} {'beats':>6}  quality")
    for r in rows:
        def s(v, f="{:.1f}"):
            return f.format(v) if v is not None else "—"
        print(f"{r['date']:12} {s(r['rmssd']):>6} {s(r['ln_rmssd'],'{:.3f}'):>8} "
              f"{s(r['hr_rest']):>7} {s(r['resp_rate']):>6} {s(r['sdnn']):>6} "
              f"{s(r['pnn50']):>6} {str(r['beats']):>6}  {r['quality']}")


def cmd_baseline(conn, _args):
    b = compute_baseline(_fetch_rows(conn))
    if b["n"] == 0:
        print("no readings yet")
        return
    print(f"rolling baseline over {b['n']} reading(s) "
          f"(meaningful at ≥{b['need']}: {'yes' if b['ready'] else 'not yet'})")
    if "mean" in b:
        print(f"  normal lnRMSSD range: {b['lo']} … {b['hi']}  (mean {b['mean']} ± {b['sd']})")
        print(f"  today ({b['today_date']}): ln {b['today']}  →  {b['status'].upper()}")


def cmd_status(conn, _args):
    """Snapshot of where the morning capture stands — no BLE, safe to run any time.

    The systemd unit runs `watch` 24/7, so `systemctl is-active` is always 'active'
    and tells you nothing about the morning. This answers the real questions: is the
    window open right now, did we already capture today, and how does today sit vs.
    the rolling baseline."""
    now = datetime.now()
    today = now.date().isoformat()
    in_window = WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR

    ensure_table(conn)
    row = conn.execute(
        "SELECT captured_at, rmssd, ln_rmssd, hr_rest, beats, quality "
        "FROM morning_hrv WHERE date=?", (today,)).fetchone()

    print(f"time    {now.strftime('%Y-%m-%d %H:%M')}  (Pi local)")
    print(f"window  {WINDOW_START_HOUR:02d}:00–{WINDOW_END_HOUR:02d}:00  →  "
          f"{'OPEN' if in_window else 'closed'}")

    if row:
        cap, rmssd, ln, hr, beats, quality = row
        t = cap[11:16] if cap and len(cap) >= 16 else cap
        print(f"today   ✓ captured {t} — RMSSD {rmssd} ms (ln {ln}), "
              f"rest HR {hr}, {beats} beats [{quality}]")
    elif in_window:
        print("today   … no reading yet — window OPEN, armed & scanning for the strap")
    else:
        when = "later this morning" if now.hour < WINDOW_START_HOUR else "tomorrow"
        print(f"today   ✗ no reading — window closed, idle until "
              f"{WINDOW_START_HOUR:02d}:00 {when}")

    b = compute_baseline(_fetch_rows(conn))
    if b["n"] == 0:
        print("base    no readings yet")
    elif "mean" in b:
        state = "meaningful" if b["ready"] else f"building {b['n']}/{b['need']}"
        print(f"base    normal ln {b['lo']}…{b['hi']}  ({state})")
        if b["today_date"] == today:
            print(f"        today ln {b['today']} → {b['status'].upper()}")


def main():
    p = argparse.ArgumentParser(description="Morning HRV baseline from the Polar H10 over BLE.")
    sub = p.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("once", help="capture right now (ignores the window)")
    po.add_argument("--duration", type=int, default=CAPTURE_SECONDS)

    pw = sub.add_parser("watch", help="run forever; capture once per morning in the window")
    pw.add_argument("--duration", type=int, default=CAPTURE_SECONDS)

    pb = sub.add_parser("backfill", help="compute a morning row from an existing session")
    pb.add_argument("session", type=int)
    pb.add_argument("--date", help="override the YYYY-MM-DD key (default: the session's date)")

    sub.add_parser("list", help="print stored morning readings")
    sub.add_parser("baseline", help="print the rolling baseline + today's status")
    sub.add_parser("status", help="window state + today's reading (no BLE, any time)")

    args = p.parse_args()
    conn = sqlite3.connect(DB_PATH)
    try:
        if args.cmd == "once":
            asyncio.run(cmd_once(conn, args))
        elif args.cmd == "watch":
            asyncio.run(cmd_watch(conn, args))
        elif args.cmd == "backfill":
            cmd_backfill(conn, args)
        elif args.cmd == "list":
            cmd_list(conn, args)
        elif args.cmd == "baseline":
            cmd_baseline(conn, args)
        elif args.cmd == "status":
            cmd_status(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
