#!/usr/bin/env bash
# Push the web dashboard to the Pi and restart it — run from your Mac.
#
#   ./deploy-dashboard.sh
#
# Copies Raspberrypi/dashboard/ to the Pi (sibling of server so it reads the
# same hr_data.db), installs Flask, and restarts the dashboard systemd service.
# You must be on the SAME network as the Pi.
set -e

# ── EDIT THESE to match your Pi (same as deploy-pi.sh) ───────────────────────
PI_HOST="carter@pi4server.local"
PI_DIR="/home/carter/projects/python/esp-polar/dashboard"   # sibling of server
# ─────────────────────────────────────────────────────────────────────────────

ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT/Raspberrypi/dashboard/"

# Ship the current trained activity model next to app.py so the timeline feature
# works on the Pi. Copied fresh each deploy so a retrain always ships the latest.
MODEL_SRC="$ROOT/data/labeled_data/activity_model.joblib"
if [ -f "$MODEL_SRC" ]; then
    cp "$MODEL_SRC" "${SRC}activity_model.joblib"
    echo "→ Bundled activity model ($(du -h "$MODEL_SRC" | cut -f1))."
else
    echo "⚠️  No activity model at $MODEL_SRC — timeline will be unavailable until you train one."
fi

echo "→ Copying $SRC to $PI_HOST:$PI_DIR ..."
# keep the Pi's own venv from being clobbered
rsync -av --exclude '.venv' --exclude '__pycache__' \
      "$SRC" "$PI_HOST:$PI_DIR/"

echo "→ Installing deps + restarting service on the Pi ..."
ssh "$PI_HOST" bash -s <<EOF
set -e
cd $PI_DIR
# ML deps (numpy/scipy/scikit-learn) can be slow or fail on a low-memory Pi.
# Don't let that abort the deploy — the dashboard degrades gracefully without
# them (activity band just won't show), so install non-fatally.
if [ -d .venv ]; then
    if .venv/bin/pip install -q -r requirements.txt; then
        echo "  deps installed."
    else
        echo "  ⚠️  some deps failed to install — dashboard will run, activity band disabled."
    fi
fi
if systemctl list-unit-files | grep -q '^dashboard.service'; then
    sudo systemctl restart dashboard
    echo "  dashboard restarted."
else
    echo "  (service not installed yet — run ./install.sh in $PI_DIR on the Pi once)"
fi
EOF

echo "✓ Done.  Open http://${PI_HOST#*@}:8000"
