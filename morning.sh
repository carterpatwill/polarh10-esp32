#!/usr/bin/env bash
# Control the morning-HRV capture (morning-hrv.service — Polar H10 → BLE → SQLite)
# on the Pi, from your Mac over SSH.
#
#   ./morning.sh start      # arm the watcher (starts the 24/7 daemon)
#   ./morning.sh stop       # stop it
#   ./morning.sh restart
#   ./morning.sh status     # daemon state + IS THE WINDOW OPEN + today's reading
#   ./morning.sh logs       # follow live logs (Ctrl-C to exit)
#
# The daemon runs `watch` around the clock but only captures inside the morning
# window (07:00–11:00), so `status` reports the *window* state and today's reading,
# not just systemd's always-"active". You must be on the SAME network as the Pi.

PI_HOST="carter@pi4server.local"                          # same as deploy-pi.sh
PI_DIR="/home/carter/projects/python/esp-polar/server"    # where the code lives on the Pi
SERVICE="morning-hrv"                                     # the systemd unit
PY="$PI_DIR/.venv/bin/python3 $PI_DIR/morning-hrv/morning_hrv.py"

CMD="${1:-status}"
TTY=""   # only 'logs' needs a real terminal

case "$CMD" in
    start)   REMOTE="sudo systemctl start $SERVICE   && echo '✓ started'"   ;;
    stop)    REMOTE="sudo systemctl stop $SERVICE    && echo '✓ stopped'"   ;;
    restart) REMOTE="sudo systemctl restart $SERVICE && echo '✓ restarted'" ;;
    status)  REMOTE="echo \"daemon:  \$(systemctl is-active $SERVICE)   (enabled on boot: \$(systemctl is-enabled $SERVICE))\";
                     echo '──';
                     $PY status" ;;
    logs)    REMOTE="journalctl -u $SERVICE -f"; TTY="-t"                   ;;
    *) echo "usage: $0 {start|stop|restart|status|logs}"; exit 2 ;;
esac

ssh $TTY -o ConnectTimeout=8 "$PI_HOST" "$REMOTE"
rc=$?

if [ $rc -eq 255 ]; then
    echo ""
    echo "❌ Can't reach the Pi at $PI_HOST."
    echo "   You must be on the SAME WiFi / network as the Pi to control it over SSH."
    exit 1
fi
exit $rc
