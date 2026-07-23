# The metrics store (real workouts)

Permanent home for sessions you recorded to **measure** — real workouts you want
numbers on (`Biking`, `5k run`, `Morning ride`), as opposed to the short labeled
examples that train the classifier (those live in `../labeled_data/`).

A session lands here when its `kind` is `metric` (set by the toggle on the
control page). Training samples (`kind = train`) go to the library instead.

## Files
- `metrics.db` — the collected real-workout sessions (created on first file-in).

## Why separate from the library
Real workouts are long and mixed-activity — great to *analyze*, poison to *train*
on. Keeping them out of `labeled_walks.db` keeps the training set clean.

See `../../docs/data-organization.md` for the full scheme (the three-store model,
the `kind` column, and how it flows from the control page to the Pi).

## Status
Scaffold only. The `kind` column, control-page toggle, and the `metrics add`
filing step are not built yet — this folder marks where that data will live.
What each workout should compute (HR summary / steps / activity breakdown) is
still to be decided.
