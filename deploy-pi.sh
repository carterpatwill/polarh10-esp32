#!/usr/bin/env bash
# Push the Raspberry Pi server code to the Pi and restart it — run from your Mac.
#
#   ./deploy-pi.sh
#
# Copies Raspberrypi/server/ to the Pi over SSH (rsync), reinstalls Python deps,
# and restarts the server systemd service. No GitHub round-trip needed.
set -e

# ── EDIT THIS to match how you SSH into your Pi ──────────────────────────────
PI_HOST="carter@pi4server.local"    # e.g. pi@192.168.1.42
PI_DIR="/home/carter/projects/python/esp-polar/server"   # where the code lives on the Pi
# ─────────────────────────────────────────────────────────────────────────────

SRC="$(cd "$(dirname "$0")" && pwd)/Raspberrypi/server/"

echo "→ Copying $SRC to $PI_HOST:$PI_DIR ..."
# --exclude keeps the Pi's own venv, database, and secrets from being clobbered
rsync -av --exclude '.venv' --exclude 'hr_data.db' --exclude 'mqtt.env' \
      "$SRC" "$PI_HOST:$PI_DIR/"

echo "→ Installing deps + restarting service on the Pi ..."
ssh "$PI_HOST" bash -s <<EOF
set -e
cd $PI_DIR
if [ -d .venv ]; then .venv/bin/pip install -q -r requirements.txt; fi
if systemctl list-unit-files | grep -q '^server.service'; then
    sudo systemctl restart server
    echo "  server restarted."
else
    echo "  (service not installed yet — run ./install.sh on the Pi once)"
fi
EOF

echo "✓ Done."
