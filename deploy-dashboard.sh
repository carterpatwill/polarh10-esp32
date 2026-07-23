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

SRC="$(cd "$(dirname "$0")" && pwd)/Raspberrypi/dashboard/"

echo "→ Copying $SRC to $PI_HOST:$PI_DIR ..."
# keep the Pi's own venv from being clobbered
rsync -av --exclude '.venv' --exclude '__pycache__' \
      "$SRC" "$PI_HOST:$PI_DIR/"

echo "→ Installing deps + restarting service on the Pi ..."
ssh "$PI_HOST" bash -s <<EOF
set -e
cd $PI_DIR
if [ -d .venv ]; then .venv/bin/pip install -q -r requirements.txt; fi
if systemctl list-unit-files | grep -q '^dashboard.service'; then
    sudo systemctl restart dashboard
    echo "  dashboard restarted."
else
    echo "  (service not installed yet — run ./install.sh in $PI_DIR on the Pi once)"
fi
EOF

echo "✓ Done.  Open http://${PI_HOST#*@}:8000"
