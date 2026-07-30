# Recovery Metrics — Design Notes

*2026-07-25 — brainstorm, not yet built.*

Track **how fast heart rate drops after effort**. Fitter cardiovascular system =
faster drop. Same idea a Whoop/Garmin sells you, but from our own Polar H10 data.

This is a **metric**, not training data — it belongs to real workouts, filed under
`kind = metric`, not the walk/jog/run classifier library. See `training-vs-metrics-split`.

New tool would be `recovery.py`, sibling to `steps.py` / `activity.py`, reusing
their signal math and the still/motion detection.

---

## The data we already have (all of it lives in the `hr` + `acc` tables)

- **`hr.bpm`** — the heart-rate curve over time (`t_ms`) → HRR and τ
- **`hr.rr_ms`** — beat-to-beat RR intervals → **HRV** (an independent recovery signal)
- **`acc`** — motion; `activity.py` already turns this into still / walk / jog / run

Cross-referencing HR and ACC is the whole trick: HR alone can't tell "peak because
I'm working hard" from "peak because effort just ended." Motion disambiguates it.

---

## A "recovery event" — the definition we settled on

1. **Start = the HR peak that lines up with activity ending.**
   Not just "max bpm in the session" (HR spikes mid-run). It's the HR maximum in
   the window *around the moment motion drops off* — jog/run → walk/still. The two
   signals check each other.

2. **The window continues** through the cooldown as long as motion stays **below jog**.
   Standing still AND walking both count as recovery (that's how I actually cool
   down — a mix). Walking during cooldown is fine; it just means HR settles toward
   a walking floor (~90–100) instead of true resting.

3. **The window ENDS when activity returns to jog or above.**
   Jog/run = "activity again," so recovery is over. Also end on a hard time cap
   (~3–5 min) if I just stand around.

### Per-event, not per-session

A single session can hold **multiple** recovery events (interval workouts!). We
compute recovery **for each event separately**, even when there are several in one
session. This is the interesting case — you can literally watch recovery slow down
as fatigue accumulates across intervals. Never collapse them into one session number
(can always average later if wanted).

### Only real efforts count

Add a floor so a gentle walk that barely raised HR doesn't spawn a bogus event:
peak must be **> X bpm above resting** to qualify as a recovery event.

---

## The metrics

Start simple, layer up.

| Metric | What | Needs resting HR? | Notes |
|---|---|---|---|
| **HRR60**  | bpm dropped in first 60 s after peak (`peak − hr@+60s`) | no | Clinical gold standard. <12 poor, >20 healthy. The headline number. |
| **HRR120** | drop over first 120 s | no | Same idea, longer, less noisy |
| **Recovery τ (tau)** | fit `HR(t) = rest + (peak−rest)·e^(−t/τ)`; τ = time constant | yes | Best single "recovery speed" number — uses the whole curve. Smaller = fitter. |
| **Time-to-baseline** | seconds to within N bpm of resting (or walking-recovery HR) | yes | Intuitive. If walking it off, "baseline" ≈ walking HR, not true rest. |

**Phase 1:** HRR60 + HRR120 (no resting HR needed, no curve fitting — dead simple).
**Phase 2:** add τ + time-to-baseline once resting HR is dialed in.

### Resting HR

τ and time-to-baseline need a floor to decay toward.
- **To nail it:** record a 2–3 min session sitting/lying totally still *before* any
  activity, label it `resting`. Resting HR = lowest stable plateau (e.g. 10th
  percentile of bpm while ACC = still).
- **Long-term:** estimate it automatically from the calmest `still` stretches across
  all sessions, so it's never hand-entered. Validate that estimate against a
  dedicated resting recording.

### Measured baseline (Pi session 36, 2026-07-25, "resting heart rate", ~2:48 min)

Clean recording — motion std 4.2 (still), 138/138 RR intervals survived cleaning,
HR-from-RR (54) matched bpm median (54).

- **Resting HR ≈ 53 bpm** (min 52, 10th-pct 52, median 54, max 59) — the decay floor.
- **Resting RMSSD ≈ 59 ms** — the HRV rebound ceiling. (SDNN 68, pNN50 43%.)

These are the Phase-2 anchors: τ / time-to-baseline decay toward 53 bpm; post-effort
RMSSD is crushed to single digits and "recovered" = climbing back toward ~59 ms.
Note: this session was tagged `kind=train`, not `metric` (toggle missed) — didn't
affect reading it.

---

## HRV — this is a first-class goal, not just a bonus

`rr_ms` gives beat-to-beat intervals, which is the raw material for HRV. HRV is
**suppressed during and right after hard effort and rebounds as you recover** —
parasympathetic (vagal) reactivation. It's a deeper autonomic signal than raw bpm,
and most consumer trackers either don't expose it or charge for it. We have the raw
RR data, so we can go further.

### What we can compute from RR intervals

- **RMSSD** — root mean square of successive RR differences. The standard
  short-window HRV metric, dominated by parasympathetic (vagal) tone. This is the
  one to build around.
- **SDNN** — standard deviation of RR intervals. Broader variability.
- **pNN50** — % of successive RR pairs differing by >50 ms.
- **HR–HRV coupling** — RMSSD naturally rises as bpm falls during recovery; tracking
  both together tells a richer story than either alone.

### HRV recovery ideas (the interesting part)

- **Post-effort RMSSD rebound curve** — RMSSD is crushed at peak, climbs back during
  cooldown. Track *how fast it climbs* per recovery event — an HRV analogue of τ.
- **RMSSD at fixed offsets** — value at +60 s, +120 s post-peak, mirroring HRR.
- **Morning / resting HRV baseline** — from a still `resting` recording; the number
  people trend day-to-day to gauge readiness/overtraining. Would want a rolling
  baseline across sessions.
- **HRV during "still" segments generally** — not just post-workout; any calm segment
  is a chance to sample resting HRV.

### HRV is finicky — respect the data cleaning

RR intervals need cleaning before HRV is trustworthy:
- **Ectopic/artifact beats** — a missed or doubled beat throws RMSSD wildly. Filter
  RR values outside a plausible range and drop >20% jumps from the previous interval.
- **Short windows are noisy** — RMSSD wants ~30–60 s of clean beats. During fast HR
  change (the steep part of recovery) HRV estimates are shakier; windowing matters.
- **`rr_ms` is stored as TEXT (JSON list)** — each `hr` row can carry several RR
  values; need to flatten them into one continuous beat-to-beat series first.
- **Motion corrupts RR** — the H10 mis-detects beats when you're moving, so HRV is
  most reliable in the still/walk cooldown, less so mid-stride.

Because of all this: **bpm-based HRR/τ is phase 1** (robust, simple), **HRV recovery
is phase 2** (higher value, more care). Both are goals; HRV is not an afterthought.

---

## What a run might print (mock)

```
Session 14  "5x sprint intervals"
Resting HR: 58 bpm (estimated)   Resting RMSSD: 42 ms

Recovery events found: 3

  #  peak   @time    HRR60   HRR120   τ(s)   to-base   RMSSD@60
  1  178    2:31     28 bpm   41 bpm   62     4:10       11 ms
  2  181    5:52     22 bpm   35 bpm   78     5:30        8 ms
  3  176    9:14     31 bpm   44 bpm   55     3:45       14 ms

→ Best recovery: event #3  (HRR60 31, τ 55s)
→ Trend: recovery slowing across intervals (fatigue), HRV rebound weakest at #2
```

---

## Open questions / TODO

- [ ] Test recording → get a real resting HR (and resting RMSSD) number.
- [ ] Pick the "real effort" floor (X bpm above resting) for what counts as an event.
- [ ] Decide the time cap for a recovery window when standing around (3? 5 min?).
- [ ] Confirm how `rr_ms` JSON is structured in practice before writing HRV cleaning.
- [ ] Storage: eventually a `recovery_metrics` table (per event) so numbers trend
      over time. Not phase 1.

## Build status

**BUILT 2026-07-25 — `data/recovery.py`** (`events` / `plot` / `list`). HR+ACC align
on the shared H10 `t_ms` clock. Reuses `activity.py`'s classifier for jog/run effort
blocks; peak = max bpm near the motion drop; window → next jog/run, 300s cap, or end.
Per-event HRR60/HRR120 + τ + time-to-baseline + RMSSD@60, all in one table. τ is
gated (dur≥45s, drop≥6bpm, R²≥0.6, τ≤600s) so short/flat windows show "—" instead of
a fake number. HRR won't interpolate across a >10s data gap. Defaults resting=53.

**τ is fit over the DESCENT only** (peak → the recovery low), not the whole window —
a window often runs into the next effort ramping back up, and that rising tail wrecks
the exponential (e.g. session 39 #4: full-window R²≈0.60 borderline-reject → descent-
only R²≈0.95, τ≈114s). When τ is still rejected but the fall is real (≥12 bpm over
≥15 s), the plot / dashboard draws a **dashed straight peak→low guide** so an obvious
recovery (a mid-workout stop that's just too noisy to fit) is shown rather than hidden;
τ stays "—". Solid line = trustworthy exponential, dashed = guide.

**Known limitation:** effort detection is **gait-based** — cycling/rowing raise HR
without a running gait, so those sessions yield no events. A future HR-rise trigger
would cover them.

### Trending — BUILT 2026-07-30
`recovery.py backfill` + `recovery.py trend` persist one row per event into a
`recovery_metrics` table (in hr_data.db) and read them back grouped by matched
effort (peak-HR buckets: moderate <150 / hard 150–170 / max ≥170). Idempotent
(delete+insert per session, no dupes). **Backfill defaults to `kind='metric'`
only** — the walk/jog/run labeling clips are `train` and would pollute the trend;
`--all` overrides. A no-arg backfill also purges rows for sessions no longer in
scope, so the table stays in sync with the filter.

Reality check 2026-07-30: only **3 real workouts have events** (18 Biking, 25
Individuals, 39 Pass rush = 53 events), all in one week — plumbing is correct but
the trend is meaningless until more real workouts (WITH a cooldown) accumulate over
weeks. Dashboard trend card deferred until there's data to show.

### Still to do
- Dashboard "recovery trend" card (API `/api/recovery/trend` + chart) — deferred, data too thin.
- Auto-estimate resting HR/RMSSD from calm `still` stretches (right now hardcoded 53/59).
- Optional: HR-rise effort trigger for non-gait workouts (bike/row).
