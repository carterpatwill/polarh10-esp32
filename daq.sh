#!/usr/bin/env bash
# Control the workout-DAQ receiver (server.service — ESP32 → MQTT → SQLite) on the
# Pi, from your Mac over SSH.
#
#   ./daq.sh start      # start ingesting
#   ./daq.sh stop       # stop it
#   ./daq.sh restart
#   ./daq.sh status     # active? enabled on boot? + last log lines
#   ./daq.sh logs       # follow live logs (Ctrl-C to exit)
#
# NOTE: you must be on the SAME network/WiFi as the Pi for this to work.

PI_HOST="carter@pi4server.local"      # same as deploy-pi.sh
SERVICE="server"                      # the workout-DAQ systemd unit

CMD="${1:-status}"
TTY=""   # only 'logs' needs a real terminal

case "$CMD" in
    start)   REMOTE="sudo systemctl start $SERVICE   && echo '✓ started'"   ;;
    stop)    REMOTE="sudo systemctl stop $SERVICE    && echo '✓ stopped'"   ;;
    restart) REMOTE="sudo systemctl restart $SERVICE && echo '✓ restarted'" ;;
    status)  REMOTE="echo \"active:  \$(systemctl is-active $SERVICE)\";
                     echo \"enabled: \$(systemctl is-enabled $SERVICE)\";
                     echo '──';
                     systemctl --no-pager status $SERVICE | sed -n '1,4p';
                     echo '── last log lines ──';
                     journalctl -u $SERVICE -n 4 --no-pager -o cat" ;;
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
