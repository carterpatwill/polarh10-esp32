#!/usr/bin/env bash
# Pull the latest recordings from the Pi, then open the labeler on THIS Mac.
#
#   ./label-local.sh
#
# Labeling + training deliberately run on the Mac, not the 1GB Pi (see the
# self-service-labeler notes). This grabs a fresh, CONSISTENT snapshot of the
# Pi's hr_data.db into data/hr_data.db and starts the dashboard locally so new
# workouts show up as label candidates. You must be on the SAME network as the Pi.
#
# NON-destructive: unlike dump-pi.sh this NEVER wipes the Pi — it only reads.
set -euo pipefail

# ── EDIT THESE to match your Pi (same as dump-pi.sh / deploy-dashboard.sh) ────
PI_HOST="carter@pi4server.local"
REMOTE_DB="/home/carter/projects/python/esp-polar/server/hr_data.db"
PORT=8000
# ─────────────────────────────────────────────────────────────────────────────

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOCAL_DB="$ROOT/data/hr_data.db"
MODEL="$ROOT/data/labeled_data/activity_model.joblib"
REMOTE_TMP="/tmp/hr_label_pull_$$.db"

# ── Same-network guard: can we reach the Pi and its DB? ──────────────────────
echo "→ Checking the Pi is reachable at $PI_HOST ..."
if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$PI_HOST" "test -f '$REMOTE_DB'" 2>/dev/null; then
    echo ""
    echo "⚠️  Can't reach the Pi (or the database) at $PI_HOST."
    echo "    Starting the dashboard with the recordings already in data/hr_data.db."
    SKIP_PULL=1
else
    SKIP_PULL=0
fi

# ── Pull a consistent snapshot (safe even while the receiver is writing) ─────
if [ "$SKIP_PULL" -eq 0 ]; then
    echo "→ Snapshotting hr_data.db on the Pi ..."
    ssh "$PI_HOST" "python3 - '$REMOTE_DB' '$REMOTE_TMP'" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)          # atomic online backup — no wipe, read-only on the source
def n(c, t):
    try: return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception: return 0
print(f"   snapshot rows — hr: {n(src,'hr')}  acc: {n(src,'acc')}")
src.close(); dst.close()
PY
    echo "→ Downloading → $LOCAL_DB ..."
    scp -q "$PI_HOST:$REMOTE_TMP" "$LOCAL_DB"
    ssh "$PI_HOST" "rm -f '$REMOTE_TMP'"
    echo "✓ Refreshed data/hr_data.db from the Pi (Pi left untouched)."
fi

# ── Build the React front-end so app.py has a bundle to serve ────────────────
# The dashboard UI is now a React app (Raspberrypi/dashboard/web) built into
# static/dist. Without this, app.py just serves a "Dashboard not built" page.
WEB="$ROOT/Raspberrypi/dashboard/web"
if command -v npm >/dev/null 2>&1; then
    echo "→ Building the dashboard front-end ..."
    ( cd "$WEB" && { [ -d node_modules ] || npm install; } && npm run build >/dev/null )
    echo "✓ Front-end built."
else
    echo "⚠️  npm not found — can't build the UI. Install Node, or run 'npm run build'"
    echo "    in $WEB once. Starting the API anyway."
fi

# ── Pick a Python that has the deps: the dashboard venv, else system python3 ──
VENV_PY="$ROOT/Raspberrypi/dashboard/.venv/bin/python"
if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import flask, numpy, sklearn, pandas" 2>/dev/null; then
    PY="$VENV_PY"
else
    PY="python3"
    echo "ℹ️  dashboard venv missing labeler deps — using system python3."
fi

# ── Free the port if a previous instance is still bound ──────────────────────
if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
    echo "→ Port $PORT busy — stopping the previous dashboard ..."
    lsof -ti "tcp:$PORT" | xargs kill 2>/dev/null || true
    sleep 1
fi

echo ""
echo "→ Starting labeler at http://localhost:$PORT/#/label   (Ctrl-C to stop)"
command -v open >/dev/null && (sleep 2; open "http://localhost:$PORT/#/label") &

cd "$ROOT/Raspberrypi/dashboard"
exec env HR_DB="$LOCAL_DB" ACTIVITY_MODEL="$MODEL" PORT="$PORT" \
     LABELER_DATA="$ROOT/data" "$PY" app.py
