# Recovery Trend — Pick Up Later

*2026-07-30. Pinned. Plumbing is done; the dashboard card is deferred until there's
enough real data to make it worth showing.*

## TL;DR

Recovery **trending** (are my recoveries getting faster over weeks?) is built as a
CLI in `data/recovery.py`. It is NOT in the dashboard yet — on purpose, because there
are only ~3 real workouts to trend. Come back and build the web card once real
workouts have accumulated.

## What's built (works today)

- **`python recovery.py backfill`** — computes + SAVES one row per recovery event into
  a `recovery_metrics` table (in `data/hr_data.db`). Idempotent (delete+insert per
  session, no dupes). Defaults to **real workouts only** (`kind='metric'`); the
  walk/jog/run labeling clips are `kind='train'` and are excluded (they're short and
  pollute the trend). `--all` includes them. A no-arg backfill also purges rows for
  sessions no longer in scope, so the table stays in sync with the filter.
- **`python recovery.py trend`** — reads the table back, grouped by **matched effort**
  (peak-HR buckets: moderate <150 / hard 150–170 / max ≥170 bpm), oldest→newest, with
  a first→last read per bucket. Compare within a bucket, not across.
- `recovery_metrics` schema: `(session, event_idx)` PK, plus `peak_t, peak_bpm, dur,
  hrr60, hrr120, tau, to_base, reached, rmssd60, resting_used, computed_at`. Stores
  RAW numbers only — effort bucketing is read-time policy (`EFFORT_BUCKETS` in
  `recovery.py`), so buckets can be re-tuned without a re-backfill.

No recovery math changed — this reuses the existing `find_events` verbatim.

## Where it's visible

| Thing | Where |
|---|---|
| **Trend across workouts** (this feature) | terminal only — `recovery.py trend` |
| **Recovery within one workout** (HRR60/τ/rate line/insight) | already in the dashboard, session detail page (`RecoveryPanel`) |

## Why it's pinned — the blocker is DATA, not code

As of 2026-07-30 only **3 real (`metric`) workouts have events**: 18 Biking (8),
25 Individuals (34), 39 Pass rush (11) = 53 events — and they're all in one week, so
there's no real time axis yet. Also **session 18 "Biking" is probably spurious**:
effort detection is gait-based, so pedaling misreads as jog/run. A trend chart against
3 dots would look empty and mean nothing.

## The loop to grow the data

1. Record a real workout tagged `kind='metric'`, and **end with a real cooldown**
   (stop moving 1–2 min) so HRR60 (needs 60 s) and τ (needs ~45 s of clean descent)
   can actually compute.
2. `cd data && python recovery.py backfill`  (idempotent — just re-run it).
3. `python recovery.py trend`  to watch events pile up per effort bucket.

Use the dashboard venv python if system python lacks sklearn:
`../Raspberrypi/dashboard/.venv/bin/python recovery.py ...`

## When we pick this back up (build the dashboard card)

Trigger: ~8–10 real workouts spread over a few weeks (enough for a visible line).

1. **API** — add `GET /api/recovery/trend` in `Raspberrypi/dashboard/api/analysis.py`
   (or a new `trend.py` blueprint): return the `recovery_metrics` rows joined to
   `sessions` (date/label), grouped/bucketed by effort. Backfill-on-read or a small
   refresh so new sessions appear.
2. **UI** — a "Recovery over time" card in the React app (`web/src/`), one small
   Chart.js line per effort bucket (reuse the `lib/charts` setup). Lead with **τ**
   (falling = fitter) and **HRR60** — they're the cleanest fitness numbers.
3. Consider persisting an auto-estimated **resting HR** instead of the hardcoded 53,
   and an **HR-rise effort trigger** so bike/row workouts stop producing gait-based
   spurious/missing events.

Related: `docs/2026-07-25-recovery-metrics.md` (full recovery design + build log),
and the training-vs-metrics `kind` split.
