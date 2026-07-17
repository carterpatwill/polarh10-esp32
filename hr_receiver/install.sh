#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Creating virtual environment..."
python3 -m venv "$DIR/.venv"

echo "Installing dependencies..."
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

echo "Done. Run manually with:"
echo "  $DIR/.venv/bin/python3 $DIR/hr_receiver.py"
echo ""

read -rp "Install as systemd service (auto-start on boot)? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
    sed "s|__DIR__|$DIR|g; s|__USER__|$(whoami)|g" \
        "$DIR/hr_receiver.service" \
        | sudo tee /etc/systemd/system/hr_receiver.service > /dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable --now hr_receiver

    echo ""
    echo "Service installed and started."
    echo "  sudo systemctl status hr_receiver    — check status"
    echo "  sudo journalctl -u hr_receiver -f    — follow live logs"
fi
