#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Creating virtual environment..."
python3 -m venv "$DIR/.venv"

echo "Installing dependencies..."
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

if [[ ! -f "$DIR/mqtt.env" ]]; then
    echo ""
    echo "No mqtt.env found — creating one from the example."
    echo ">>> EDIT $DIR/mqtt.env with your HiveMQ Cloud credentials before running. <<<"
    cp "$DIR/mqtt.env.example" "$DIR/mqtt.env"
fi

echo "Done. Run manually with:"
echo "  set -a; source $DIR/mqtt.env; set +a"
echo "  $DIR/.venv/bin/python3 $DIR/server.py"
echo ""

read -rp "Install as systemd service (auto-start on boot)? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
    sed "s|__DIR__|$DIR|g; s|__USER__|$(whoami)|g" \
        "$DIR/server.service" \
        | sudo tee /etc/systemd/system/server.service > /dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable --now server

    echo ""
    echo "Service installed and started."
    echo "  sudo systemctl status server    — check status"
    echo "  sudo journalctl -u server -f    — follow live logs"
fi
