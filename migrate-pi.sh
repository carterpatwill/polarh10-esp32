#!/usr/bin/env bash
# ONE-TIME migration: reshape the Pi's flat server/ into the split layout
# (server/workout-daq/ + server/morning-hrv/, sharing one .venv + hr_data.db).
# Run once from your Mac after the repo split; afterwards use ./deploy-pi.sh as usual.
#
#   ./migrate-pi.sh
#
# It is idempotent — safe to re-run. It NEVER touches .venv, hr_data.db, or your
# credentials value (mqtt.env is moved, not overwritten). It shows the before/after
# tree and asks for confirmation before the destructive steps (moving mqtt.env,
# reinstalling the units, deleting the old flat files).
set -euo pipefail

# ── EDIT THESE to match how you SSH into your Pi (same as deploy-pi.sh) ───────
PI_HOST="carter@pi4server.local"
PI_DIR="/home/carter/projects/python/esp-polar/server"
# ─────────────────────────────────────────────────────────────────────────────

SRC="$(cd "$(dirname "$0")" && pwd)/Raspberrypi/server/"

# Old flat files rsync leaves behind (parent-level only — subfolder copies are the
# new home; hr_data.db*/mqtt.env/.venv are deliberately NOT in this list).
STALE=(server.py morning_hrv.py server.service morning_hrv.service mqtt.env.example)

tree_cmd() {
    # Compact view of server/, hiding the venv / caches / db backups.
    ssh "$PI_HOST" "cd '$PI_DIR' && find . -maxdepth 2 \
        -not -path './.venv/*' -not -path './__pycache__/*' \
        -not -name 'hr_data.db.*' | sort"
}

echo "════════════════════════════════════════════════════════════════════"
echo " BEFORE  ($PI_HOST:$PI_DIR)"
echo "════════════════════════════════════════════════════════════════════"
tree_cmd

echo
echo "→ rsync new split layout into place (adds subfolders; deletes nothing) ..."
rsync -av --exclude '.venv' --exclude 'hr_data.db' --exclude 'mqtt.env' \
      "$SRC" "$PI_HOST:$PI_DIR/"

echo
echo "The next steps are DESTRUCTIVE on the Pi:"
echo "  • move  mqtt.env → workout-daq/mqtt.env   (credentials preserved)"
echo "  • reinstall systemd units  server.service + morning-hrv.service  (new paths)"
echo "  • delete old flat files:   ${STALE[*]}"
echo "  • restart both services"
read -rp "Proceed? [y/N] " reply
[[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted — nothing destructive was done."; exit 0; }

echo
echo "→ Migrating on the Pi ..."
ssh "$PI_HOST" bash -s -- "$PI_DIR" "${STALE[@]}" <<'REMOTE'
set -euo pipefail
PI_DIR="$1"; shift
STALE=("$@")
USER_NAME="$(whoami)"
cd "$PI_DIR"

# 1. Move the credentials file into workout-daq/ (never clobber an existing one).
if [ -f mqtt.env ] && [ ! -f workout-daq/mqtt.env ]; then
    mv mqtt.env workout-daq/mqtt.env
    echo "  moved mqtt.env → workout-daq/mqtt.env"
elif [ -f workout-daq/mqtt.env ]; then
    echo "  workout-daq/mqtt.env already present — leaving it"
    [ -f mqtt.env ] && { rm -f mqtt.env; echo "  removed now-duplicate parent mqtt.env"; }
else
    echo "  ⚠ no mqtt.env found — create workout-daq/mqtt.env from mqtt.env.example"
fi

# 2. Refresh the shared venv's deps (paho-mqtt + bleak + numpy).
if [ -d .venv ]; then
    .venv/bin/pip install -q -r requirements.txt
    echo "  deps up to date in shared .venv"
fi

# 3. Reinstall both systemd units pointed at the new subfolder paths.
install_unit() {  # <installed-name> <template>
    sed "s|__ROOT__|$PI_DIR|g; s|__USER__|$USER_NAME|g" "$2" \
        | sudo tee "/etc/systemd/system/$1" > /dev/null
    echo "  reinstalled $1"
}
install_unit server.service      workout-daq/server.service
install_unit morning-hrv.service morning-hrv/morning_hrv.service
sudo systemctl daemon-reload

# 4. Delete the old flat files (parent level only) + stale bytecode cache.
for f in "${STALE[@]}"; do
    if [ -e "$f" ]; then rm -f "$f"; echo "  removed stale $f"; fi
done
[ -d __pycache__ ] && rm -rf __pycache__ && echo "  removed stale __pycache__"

# 5. Restart both services on the new layout.
for unit in server morning-hrv; do
    sudo systemctl enable --now "$unit" >/dev/null 2>&1 || true
    sudo systemctl restart "$unit"
    echo "  restarted $unit"
done
REMOTE

echo
echo "════════════════════════════════════════════════════════════════════"
echo " AFTER  ($PI_HOST:$PI_DIR)"
echo "════════════════════════════════════════════════════════════════════"
tree_cmd

echo
echo "→ Service status:"
ssh "$PI_HOST" 'for u in server morning-hrv; do printf "  %-14s %s\n" "$u" "$(systemctl is-active "$u")"; done'

echo
echo "✓ Migration done. Verify logs if needed:"
echo "    ssh $PI_HOST 'journalctl -u server -n 20 --no-pager'"
echo "    ssh $PI_HOST 'journalctl -u morning-hrv -n 20 --no-pager'"
