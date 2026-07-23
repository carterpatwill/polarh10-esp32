#!/usr/bin/env bash
# One-time setup for the web dashboard on the Pi. Mirrors server/install.sh.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Creating virtual environment..."
python3 -m venv "$DIR/.venv"

echo "Installing dependencies..."
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

echo "Done. Run manually with:"
echo "  $DIR/.venv/bin/python3 $DIR/app.py"
echo "  then open http://$(hostname).local:8000"
echo ""

read -rp "Install as systemd service (auto-start on boot)? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
    sed "s|__DIR__|$DIR|g; s|__USER__|$(whoami)|g" \
        "$DIR/dashboard.service" \
        | sudo tee /etc/systemd/system/dashboard.service > /dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable --now dashboard

    echo ""
    echo "Dashboard installed and started."
    echo "  Open       : http://$(hostname).local:8000"
    echo "  Status     : sudo systemctl status dashboard"
    echo "  Live logs  : sudo journalctl -u dashboard -f"
fi
