#!/usr/bin/env bash
# Back up the Pi's database to this computer, then wipe it clean for a fresh run.
# Run from your Mac — you must be on the SAME WiFi / network as the Pi.
#
#   ./dump-pi.sh          # dump + clear (asks for confirmation before wiping)
#   ./dump-pi.sh -y       # dump + clear without the confirmation prompt
#
# What it does, in order:
#   1. Refuses to run unless the Pi is reachable over SSH (same-network guard).
#   2. Pulls a CONSISTENT snapshot of hr_data.db down to data/dumps/ (timestamped)
#      and refreshes data/hr_data.db so data/analyze.py works immediately.
#   3. Only AFTER the local copy is verified non-empty, clears both tables on the Pi.
set -euo pipefail

# ── EDIT THESE to match your Pi (same as deploy-pi.sh / pi-server.sh) ─────────
PI_HOST="carter@pi4server.local"
REMOTE_DB="/home/carter/projects/python/esp-polar/hr_receiver/hr_data.db"
# ─────────────────────────────────────────────────────────────────────────────

ASSUME_YES=0
if [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ]; then
    ASSUME_YES=1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DUMP_DIR="$SCRIPT_DIR/data/dumps"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOCAL_ARCHIVE="$DUMP_DIR/hr_data_$STAMP.db"
LOCAL_LATEST="$SCRIPT_DIR/data/hr_data.db"
REMOTE_TMP="/tmp/hr_dump_$STAMP.db"

# ── 1. Same-WiFi guard: can we reach the Pi at all? ──────────────────────────
echo "→ Checking the Pi is reachable at $PI_HOST ..."
if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$PI_HOST" "test -f '$REMOTE_DB'" 2>/dev/null; then
    echo ""
    echo "❌ Can't reach the Pi (or the database file) at $PI_HOST."
    echo "   You must be on the SAME WiFi / network as the Pi to run this."
    echo "   Nothing was dumped and nothing was cleared."
    exit 1
fi

mkdir -p "$DUMP_DIR"

# ── 2. Take a consistent snapshot on the Pi, then pull it down ────────────────
echo "→ Snapshotting the database on the Pi (consistent, even while it's writing) ..."
ssh "$PI_HOST" "python3 - '$REMOTE_DB' '$REMOTE_TMP'" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)          # atomic online backup — safe under concurrent writes
def n(c, t):
    try: return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception: return 0
print(f"   snapshot rows — readings: {n(src,'readings')}  acc: {n(src,'acc')}")
src.close(); dst.close()
PY

echo "→ Downloading snapshot to $LOCAL_ARCHIVE ..."
scp -q "$PI_HOST:$REMOTE_TMP" "$LOCAL_ARCHIVE"
ssh "$PI_HOST" "rm -f '$REMOTE_TMP'"

# ── verify the local copy exists and isn't empty BEFORE we wipe anything ──────
if [ ! -s "$LOCAL_ARCHIVE" ]; then
    echo "❌ Downloaded file is missing or empty — refusing to clear the Pi."
    exit 1
fi
cp "$LOCAL_ARCHIVE" "$LOCAL_LATEST"
echo "✓ Saved: $LOCAL_ARCHIVE"
echo "✓ Refreshed: $LOCAL_LATEST  (data/analyze.py reads this)"

# ── 3. Confirm, then clear both tables on the Pi ──────────────────────────────
if [ "$ASSUME_YES" -ne 1 ]; then
    printf "\n⚠️  Wipe ALL rows on the Pi now? Local backup is saved above. [y/N] "
    read -r reply
    case "$reply" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "Skipped clearing. Backup kept; Pi left untouched."; exit 0 ;;
    esac
fi

echo "→ Clearing readings + acc on the Pi ..."
ssh "$PI_HOST" "python3 - '$REMOTE_DB'" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("readings","acc")}
for t in ("readings","acc"):
    c.execute(f"DELETE FROM {t}")
c.execute("DELETE FROM sqlite_sequence")
c.commit(); c.execute("VACUUM")
after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("readings","acc")}
c.close()
print(f"   readings: {before['readings']} -> {after['readings']}")
print(f"   acc:      {before['acc']} -> {after['acc']}")
PY

echo "✓ Done. Pi is fresh; your data is backed up locally."
