#!/usr/bin/env bash
set -e

# server/ is the shared root: one .venv + one hr_data.db, with the two apps in
# subfolders (workout-daq/, morning-hrv/). Run this from the server/ folder on the Pi.
ROOT="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(whoami)"

echo "Creating virtual environment..."
python3 -m venv "$ROOT/.venv"

echo "Installing dependencies (paho-mqtt + bleak + numpy)..."
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements.txt"

if [[ ! -f "$ROOT/workout-daq/mqtt.env" ]]; then
    echo ""
    echo "No workout-daq/mqtt.env found — creating one from the example."
    echo ">>> EDIT $ROOT/workout-daq/mqtt.env with your HiveMQ Cloud credentials before running. <<<"
    cp "$ROOT/workout-daq/mqtt.env.example" "$ROOT/workout-daq/mqtt.env"
fi

echo "Done. Run manually with:"
echo "  # workout DAQ (ESP32 → MQTT → SQLite):"
echo "  set -a; source $ROOT/workout-daq/mqtt.env; set +a"
echo "  $ROOT/.venv/bin/python3 $ROOT/workout-daq/server.py"
echo "  # morning HRV (Polar H10 → BLE → SQLite):"
echo "  $ROOT/.venv/bin/python3 $ROOT/morning-hrv/morning_hrv.py watch"
echo ""

# install_unit <unit-name> <path-to-.service-template>
install_unit() {
    local name="$1" src="$2"
    sed "s|__ROOT__|$ROOT|g; s|__USER__|$USER_NAME|g" "$src" \
        | sudo tee "/etc/systemd/system/$name" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now "${name%.service}"
    echo "  installed & started $name"
    echo "    sudo systemctl status ${name%.service}    — check status"
    echo "    sudo journalctl -u ${name%.service} -f     — follow live logs"
}

read -rp "Install workout-daq (server.service) as a systemd service? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
    install_unit server.service "$ROOT/workout-daq/server.service"
fi

read -rp "Install morning-hrv (morning-hrv.service) as a systemd service? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
    install_unit morning-hrv.service "$ROOT/morning-hrv/morning_hrv.service"
fi
