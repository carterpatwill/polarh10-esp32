# Data organization — training vs. metrics

Every recorded session exists for **one of two reasons**. Keeping them apart is
the whole idea; mixing them is what makes a dataset rot.

| | **Training sample** | **Metrics session** |
|---|---|---|
| Why you recorded it | To *teach* the classifier what walk/jog/run/sprint looks like | To *measure* a real workout and get numbers back |
| Shape | Short, **one** activity, controlled | Long, **mixed** activity, real-world |
| Label | Gait word (+ step count): `Jog 50` | Free name: `Morning ride`, `5k run` |
| Destination | The **library** (`labeled_data/labeled_walks.db`) | The **metrics store** (`metrics_data/metrics.db`) |
| Feeds | `activity.py train` | analysis / metrics (TBD) |
| Example | `Walk 100`, `Slow walk 30`, `Run 20` | `Biking` (28-min real ride) |

## Three stores, one idea

The dump is a sorting hat; each session goes left (train) or right (measure).

```
                         ┌─────────────────────────────┐
   ESP32 ── MQTT ──▶ Pi  │  hr_data.db  (THE DUMP)      │  temporary, overwritten each pull
                         └──────────────┬──────────────┘
                                        │ sort by  kind
                          ┌─────────────┴──────────────┐
                          ▼                             ▼
       labeled_data/labeled_walks.db          metrics_data/metrics.db
             (THE LIBRARY)                         (THE METRICS STORE)
        permanent training samples             permanent real workouts
        →  activity.py train                   →  metrics/analysis (later)
```

## The mechanism: a `kind` column

Each `sessions` row carries `kind`:

- `train`  — a labeled example for the classifier → files into the **library**
- `metric` — a real workout you want numbers on → files into the **metrics store**

Default is `metric` (a session you didn't explicitly mark as training is, by
definition, just a workout). You choose it with a **toggle on the control page**
when you start recording, right next to the label field.

## What changes, file by file — BUILT (path A), 2026-07-22

1. **`sessions` schema** (server.py `init_db`) — ✅ `kind TEXT DEFAULT 'metric'`,
   added via the existing `ALTER TABLE ADD COLUMN` backfill pattern.
2. **Pi `server.py`** — ✅ subscribes to `polar/session_cmd`, `handle_cmd` stashes
   `pending_kind` from a `start` command, `handle_session` stores it on INSERT.
3. **`web/control.html`** — ✅ Training/Metrics toggle; `session()` puts `kind` on
   the `session_cmd` start message. (Static file — just reopen it.)
4. **`data/activity.py`** — ✅ `add` skips `kind='metric'` sessions (with a clear
   reason); `--as <bucket>` still force-adds one. Old dumps without the column
   behave exactly as before.
5. **metrics store** — `metrics_data/metrics.db`, populated by a future
   `metrics add` step that pulls `kind='metric'` sessions from the dump. **Not
   built yet** (deferred with the metric math).

### How `kind` reaches the Pi — path A (no reflash, chosen)

`kind` is metadata the Pi stores — it does **not** gate the ESP's streaming, so
it doesn't travel through the firmware. The control page already publishes to
`polar/session_cmd`; the Pi now *also* subscribes there, stashes `kind` from a
`start` command, and applies it when the ESP's `polar/session` start mark arrives.
ESP firmware untouched. (Rejected alternative: relay `kind` through the ESP like
`label`, which would need reflashing `esp32/`.)

## Deferred (decide later)

What the metrics side actually computes for a real workout — candidates:
HR summary (avg/max, zones), step count (`steps.py`), activity breakdown from the
trained model (% time walking/jogging/running/biking). Storage + separation come
first; the math comes after.
