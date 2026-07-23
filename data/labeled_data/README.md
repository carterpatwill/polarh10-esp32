# Labeled training data (the "library")

Your permanent, growing collection of labeled example walks. Kept here because
`dump-pi.sh` overwrites `../hr_data.db` on every pull — this folder is safe.

## Files
- `labeled_walks.db` — the example sessions. Each label's keyword decides its
  bucket: `walk` / `jog` / `run` / `sprint`, plus `other` (a catch-all for
  non-gait motion — label it `sit`/`stand`/`rest`/`idle`/`other`).
- `activity_model.joblib` — the trained activity guesser (made by `activity.py train`).
- `plot_steps.py` — draws each walk with detected steps as red dots.
- `steps_plot.png` — the latest such plot.

## Two databases, one idea
- **The dump** `../hr_data.db` — latest data pulled off the Pi. Temporary.
- **The library** `labeled_walks.db` — permanent training data. This folder.

## The activity workflow (run from `data/`)
```
python activity.py add        # file the dump's labeled sessions into the library
python activity.py buckets    # see how many sessions each bucket has
python activity.py train      # learn walk/jog/run/sprint
python activity.py guess      # guess a new recording
python activity.py demo       # guess a random library session, reveal the answer
```
`add --as run 5` force-files dump session 5 into the run bucket if its label
has no keyword.

## Plot the steps
```
python plot_steps.py          # grid of every labeled walk → steps_plot.png
python plot_steps.py 16       # just session 16, full size
```

## Growing the data
Record more labeled walks (put the activity word in the label), then:
`./dump-pi.sh` → `python activity.py add` → `python activity.py train`.
Priorities right now: more **run**, and any **sprint** (with the accelerometer live).
