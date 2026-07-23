#!/usr/bin/env bash
# Control the Raspberry Pi server service over SSH — run from your Mac.
#
#   ./pi-server.sh start      # start the receiver
#   ./pi-server.sh stop       # stop the receiver
#   ./pi-server.sh restart    # restart it
#   ./pi-server.sh status     # is it running?
#   ./pi-server.sh logs        # follow live logs (Ctrl-C to exit)
#
# Add a second arg to control the web dashboard instead of the receiver:
#   ./pi-server.sh status dashboard
#   ./pi-server.sh restart dashboard
#
# NOTE: you must be on the SAME network/WiFi as the Pi for this to work.

PI_HOST="carter@pi4server.local"      # same as deploy-pi.sh
SERVICE="${2:-server}"           # server (default) or dashboard

CMD="${1:-status}"
TTY=""   # only 'logs' needs a real terminal

case "$CMD" in
    start)   REMOTE="sudo systemctl start $SERVICE   && echo '✓ started'"  ;;
    stop)    REMOTE="sudo systemctl stop $SERVICE    && echo '✓ stopped'"  ;;
    restart) REMOTE="sudo systemctl restart $SERVICE && echo '✓ restarted'";;
    status)  REMOTE="systemctl --no-pager status $SERVICE | head -6"        ;;
    logs)    REMOTE="journalctl -u $SERVICE -f"; TTY="-t"                   ;;
    *) echo "usage: $0 {start|stop|restart|status|logs} [server|dashboard]"; exit 2 ;;
esac

ssh $TTY -o ConnectTimeout=8 "$PI_HOST" "$REMOTE"
rc=$?

# SSH exit code 255 = couldn't establish the connection (host unreachable / not resolvable)
if [ $rc -eq 255 ]; then
    echo ""
    echo "❌ Can't reach the Pi at $PI_HOST."
    echo "   You must be on the SAME WiFi / network as the Pi to control it over SSH."
    echo "   (The Pi is at home behind your router — this won't work from the field.)"
    exit 1
fi

exit $rc
