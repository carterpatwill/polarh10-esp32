#!/usr/bin/env bash
# Spin up the dashboard on THIS Mac against the local recordings.
#
#   ./dashboard-local.sh
#
# Unlike label-local.sh this does NOT touch the Pi — it just builds the React
# front-end and serves the dashboard from the DB already in data/hr_data.db.
# Use label-local.sh when you want to pull a fresh snapshot from the Pi first.
set -euo pipefail

PORT=8000

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOCAL_DB="$ROOT/data/hr_data.db"
MODEL="$ROOT/data/labeled_data/activity_model.joblib"
WEB="$ROOT/Raspberrypi/dashboard/web"

if [ ! -f "$LOCAL_DB" ]; then
    echo "⚠️  No local database at $LOCAL_DB."
    echo "    Run ./label-local.sh (or ./dump-pi.sh) once to pull recordings from the Pi."
    exit 1
fi

# ── Build the React front-end so app.py has a bundle to serve ────────────────
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
    echo "ℹ️  dashboard venv missing deps — using system python3."
fi

# ── Free the port if a previous instance is still bound ──────────────────────
if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
    echo "→ Port $PORT busy — stopping the previous dashboard ..."
    lsof -ti "tcp:$PORT" | xargs kill 2>/dev/null || true
    sleep 1
fi

echo ""
echo "→ Starting dashboard at http://localhost:$PORT/   (Ctrl-C to stop)"
command -v open >/dev/null && (sleep 2; open "http://localhost:$PORT/") &

cd "$ROOT/Raspberrypi/dashboard"
exec env HR_DB="$LOCAL_DB" ACTIVITY_MODEL="$MODEL" PORT="$PORT" \
     LABELER_DATA="$ROOT/data" "$PY" app.py
