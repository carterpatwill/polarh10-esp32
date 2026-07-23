# `data/` — analyzing your Polar H10 recordings

This folder is where recorded sessions get turned into answers. Two main tools:

| Tool | Question it answers | Example answer |
|------|---------------------|----------------|
| `steps.py`    | **How many steps?** (a number)      | "47 steps" |
| `activity.py` | **What activity?** (a bucket)       | "WALK, 100%" |

Both read the accelerometer data your ESP32 streamed from the Polar H10.

---

## The one idea to hold in your head: two databases

- **The dump** — `hr_data.db`
  The latest data pulled off the Pi. **Temporary** — `../dump-pi.sh` overwrites
  it every time you pull. Never keep anything important only here.

- **The library** — `labeled_data/labeled_walks.db`
  Your **permanent, growing** collection of labeled example walks. Safe from
  dumps. This is what `activity.py` learns from.

Timestamped backups of every dump also pile up in `dumps/`.

---

## Setup (once per terminal window)

Everything runs from **this `data/` folder**, with the project's virtual
environment active:

```bash
cd data
source ../.venv/bin/activate      # you'll see (.venv) appear in your prompt
```

If you ever see `ModuleNotFoundError: No module named 'numpy'`, the venv isn't
active — run the `source` line again. (Or skip activating and use
`../.venv/bin/python activity.py ...` directly.)

---

## Labeling rule (this is what makes everything work)

When you start a session on the control page, the **label** carries the truth:

- For **step counting**, put the number of steps: `walk 30`, `jog 50 steps`.
- For **activity**, include the activity word: `walk`, `jog`, `run`, `sprint`.
  Any label containing "walk" → the **walk** bucket, and so on.
- For motion that isn't any of those (sitting, arm waving, fidgeting), use the
  **`other`** catch-all bucket: label it `sit`, `stand`, `rest`, `idle`, or `other`.
  This gives the guesser a "none of the gait ones" option.
- You can do both at once: `Run 40` means bucket=run, true steps=40.
- **Don't put stray numbers in non-step labels** (`warmup 2` reads as "2 steps").
  For non-activity sessions, keep the label plain (`warmup`, not `lap 3`).

---

## Tool 1 — `steps.py` (counting steps)

Learns what one step looks like from walks where you counted, then counts new ones.

```bash
python steps.py list         # sessions + the true step count read from each label
python steps.py calibrate    # tune the detector on your labeled walks
python steps.py count        # count steps in the latest session
python steps.py count 14     # ... a specific session id
python steps.py plot         # draw the wave with each detected step marked (red dots)
```

Calibration is saved to `steps_model.json`. `activity.py` reuses these settings,
so **run `calibrate` at least once** before using `activity.py`.

---

## Tool 2 — `activity.py` (walk / jog / run / sprint / other)

Sorts motion into five buckets. All your different walk recordings fold into the
one `walk` bucket automatically. `other` is the catch-all for non-gait motion
(sitting, fidgeting) so the guesser isn't forced to pick a gait when none fits.

```bash
python activity.py add        # file the dump's labeled sessions into the library
python activity.py buckets    # how many example sessions each bucket has
python activity.py train      # learn walk/jog/run/sprint from the library
python activity.py guess      # guess the latest recording, second-by-second
python activity.py guess 14   # ... a specific session
python activity.py demo       # pick a RANDOM library session, guess it, reveal the answer
```

- `add` skips sessions already saved and ones with no accelerometer data.
- If a label has no activity keyword, force it in:
  `python activity.py add 5 --as run`
- The trained guesser is saved to `labeled_data/activity_model.joblib`.

---

## The everyday workflow (after recording new sessions)

```bash
# 1. Pull the data down (run from the repo root, on the SAME WiFi as the Pi)
cd ..
./dump-pi.sh

# 2. Into the data folder, activate the venv
cd data
source ../.venv/bin/activate

# 3. File the new sessions into your library
python activity.py add
python activity.py buckets        # sanity-check they landed in the right bucket

# 4. Re-learn from the bigger library
python activity.py train

# 5. Use it
python activity.py guess          # or: demo
```

For step counting, after a dump you'd instead run `python steps.py calibrate`
then `python steps.py count`.

---

## What's in this folder

```
steps.py            step counter (how many steps)
activity.py         activity guesser (walk/jog/run/sprint)
analyze.py          plot one session's heart rate + accelerometer
plot.py             older plotting helper
har.py              earlier activity-recognition experiment (superseded by activity.py)
steps_model.json    saved step-detector settings (from steps.py calibrate)
hr_data.db          THE DUMP — latest pull from the Pi (temporary)
dumps/              timestamped backups of past dumps
labeled_data/       THE LIBRARY — permanent training data + trained model + step plots
```

---

## Current status (as of the last training)

- **Walk**: recognized perfectly.
- **Jog**: ~95% accurate.
- **Run**: needs more data — only one run recording so far.
- **Sprint**: empty bucket — record some (with the accelerometer streaming).

Biggest win next time out: a few more **run** sessions and a couple of **sprint** ones.
