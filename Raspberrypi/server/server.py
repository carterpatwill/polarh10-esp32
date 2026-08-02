#!/usr/bin/env python3
"""Subscribes to HR batches from HiveMQ Cloud, prints them, and saves to hr_data.db (SQLite).

Config comes from environment variables so credentials aren't hard-coded here.
Set them to match esp32/src/config.h before running, e.g.:

    export MQTT_HOST="YOUR-CLUSTER.s1.eu.hivemq.cloud"
    export MQTT_PORT=8883
    export MQTT_USER="YOUR_MQTT_USERNAME"
    export MQTT_PASS="YOUR_MQTT_PASSWORD"
    export MQTT_TOPIC="polar/hr"
"""

import json
import os
import sqlite3
import ssl
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

MQTT_HOST      = os.environ.get("MQTT_HOST",  "YOUR-CLUSTER.s1.eu.hivemq.cloud")
MQTT_PORT      = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER      = os.environ.get("MQTT_USER",  "YOUR_MQTT_USERNAME")
MQTT_PASS      = os.environ.get("MQTT_PASS",  "YOUR_MQTT_PASSWORD")
MQTT_TOPIC     = os.environ.get("MQTT_TOPIC",     "polar/hr")
MQTT_TOPIC_ACC = os.environ.get("MQTT_TOPIC_ACC", "polar/acc")
MQTT_TOPIC_PI      = os.environ.get("MQTT_TOPIC_PI",      "pi/status")     # heartbeat we publish
MQTT_TOPIC_SESSION = os.environ.get("MQTT_TOPIC_SESSION", "polar/session") # start/stop we receive (from ESP, carries label)
MQTT_TOPIC_CMD     = os.environ.get("MQTT_TOPIC_CMD",     "polar/session_cmd") # control page → ESP; we also read it for `kind`
MQTT_TOPIC_ACK     = os.environ.get("MQTT_TOPIC_ACK",     "polar/ack")         # we publish per-session delete confirmations here

HEARTBEAT_S = 5   # publish pi/status this often

DB_PATH = Path(__file__).parent / "hr_data.db"

# Live state shared with the heartbeat thread.
current_session_id = None            # id of the session we're actively ingesting (heartbeat/legacy)
last_write_iso     = None            # ISO time of the most recent DB insert
pending_kind       = "metric"        # kind ('train'/'metric') for the next session to open;
                                     # set from the control page's session_cmd (path A, no ESP reflash)

# Per-recording upload state, keyed by the ESP32's stable upload id (uid).
#   uid -> {"sid": <sessions.id>, "complete": <bool, session finalized+stored>}
# Data messages self-identify with their uid, so nothing depends on message order:
# a lost/late "start" is recovered by lazily creating the row, and a re-upload of a
# completed recording is recognised and ignored (then re-acked) instead of duplicated.
uploads = {}


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hr (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            received  TEXT    NOT NULL,
            t_ms      INTEGER NOT NULL,
            bpm       INTEGER NOT NULL,
            rr_ms     TEXT                -- JSON array, e.g. '[1109, 1132]', or NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS acc (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            received  TEXT    NOT NULL,
            t_ms      INTEGER NOT NULL,   -- ESP32 frame receipt time (ms)
            x         INTEGER NOT NULL,   -- milli-g
            y         INTEGER NOT NULL,
            z         INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            started  TEXT    NOT NULL,
            ended    TEXT,                -- NULL while the session is still open
            label    TEXT,                -- user-supplied name for the session, or NULL
            kind     TEXT DEFAULT 'metric' -- 'train' (ML example) or 'metric' (real workout)
        )
    """)
    # Tag data rows with the session they belong to. ADD COLUMN is a no-op error
    # on DBs that already have it, so ignore that specific failure.
    for table in ("hr", "acc"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN session INTEGER")
        except sqlite3.OperationalError:
            pass
    # Backfill columns on DBs created before they existed (each a no-op if present).
    # upload_uid is the ESP32's stable per-recording id; it lets a re-uploaded file
    # map back to its existing session row instead of spawning a duplicate.
    for ddl in ("ALTER TABLE sessions ADD COLUMN label TEXT",
                "ALTER TABLE sessions ADD COLUMN kind TEXT DEFAULT 'metric'",
                "ALTER TABLE sessions ADD COLUMN upload_uid INTEGER"):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"Connected to {MQTT_HOST}:{MQTT_PORT} — subscribing to "
              f"'{MQTT_TOPIC}', '{MQTT_TOPIC_ACC}', '{MQTT_TOPIC_SESSION}', '{MQTT_TOPIC_CMD}'")
        client.subscribe(MQTT_TOPIC)
        client.subscribe(MQTT_TOPIC_ACC)
        client.subscribe(MQTT_TOPIC_SESSION)
        client.subscribe(MQTT_TOPIC_CMD)
    else:
        print(f"Connection failed: {reason_code}")


def handle_cmd(data):
    """The control page's start/stop command. We don't act on it (the ESP does that
    and relays the real mark on polar/session) — we only capture `kind` so the
    session the ESP is about to open gets tagged train vs metric. No firmware needed."""
    global pending_kind
    if data.get("action") == "start":
        kind = data.get("kind")
        pending_kind = kind if kind in ("train", "metric") else "metric"


def _epoch_to_started(epoch, received):
    """Render the ESP32's reconstructed Unix start time in local time to match
    `received`. Absent/bogus epoch → fall back to sync-receipt time."""
    try:
        return datetime.fromtimestamp(epoch).isoformat(timespec="seconds") if epoch else received
    except (TypeError, ValueError, OSError, OverflowError):
        return received


def _get_session_by_uid(conn, uid):
    """Return (sid, complete) for an existing session with this uid, or None."""
    row = conn.execute("SELECT id, ended FROM sessions WHERE upload_uid=?", (uid,)).fetchone()
    return (row[0], row[1] is not None) if row else None


def _sid_for_data(conn, uid, received):
    """Session id to tag an incoming HR/ACC batch with, or None to skip storing.

    uid is None (legacy firmware) → fall back to the global open session.
    uid is known-complete         → None, so a re-sent finished recording is ignored.
    uid unseen (start lost/late)  → lazily create the session so nothing orphans.
    """
    if uid is None:
        return current_session_id
    u = uploads.get(uid)
    if u is None:
        existing = _get_session_by_uid(conn, uid)
        if existing:
            sid, complete = existing
        else:
            cur = conn.execute(
                "INSERT INTO sessions (started, kind, upload_uid) VALUES (?, ?, ?)",
                (received, pending_kind, uid))
            sid, complete = cur.lastrowid, False
            conn.commit()
        u = uploads[uid] = {"sid": sid, "complete": complete}
    return None if u["complete"] else u["sid"]


def _publish_ack(client, uid, hr, acc):
    """Tell the ESP32 how many rows we have stored for this recording. It deletes
    the file only once this covers what it sent."""
    client.publish(MQTT_TOPIC_ACK, json.dumps({"uid": uid, "hr": hr, "acc": acc}))


def handle_session(client, data, received):
    """Open/close a session by its uid and, on stop, ack what we stored so the
    ESP32 can safely delete the file."""
    global current_session_id
    action = data.get("action")
    uid    = data.get("uid")

    # Legacy firmware (no uid): keep the old single-global-session behaviour.
    if uid is None:
        return _handle_session_legacy(data, received, action)

    with sqlite3.connect(DB_PATH) as conn:
        if action == "start":
            label = (data.get("label") or "").strip() or None
            started = _epoch_to_started(data.get("started_epoch"), received)
            existing = _get_session_by_uid(conn, uid)
            if existing and existing[1]:
                # Already stored and finalized before — a retry whose ack was lost.
                # Don't touch the data; the stop will just re-ack it.
                sid = existing[0]
                uploads[uid] = {"sid": sid, "complete": True}
                print(f"[session] START uid={uid} → already complete id={sid}, will re-ack")
            else:
                if existing:                       # resume of a partial upload: start fresh
                    sid = existing[0]
                    conn.execute("DELETE FROM hr  WHERE session=?", (sid,))
                    conn.execute("DELETE FROM acc WHERE session=?", (sid,))
                    conn.execute("UPDATE sessions SET started=?, label=?, kind=?, ended=NULL WHERE id=?",
                                 (started, label, pending_kind, sid))
                else:
                    cur = conn.execute(
                        "INSERT INTO sessions (started, label, kind, upload_uid) VALUES (?, ?, ?, ?)",
                        (started, label, pending_kind, uid))
                    sid = cur.lastrowid
                conn.commit()
                uploads[uid] = {"sid": sid, "complete": False}
                current_session_id = sid
                print(f"[session] START uid={uid} → id={sid} at {started}"
                      + f"  kind={pending_kind}" + (f"  label={label!r}" if label else ""))

        elif action == "stop":
            u = uploads.get(uid)
            if u is None:                          # data seen but start+stop only now
                existing = _get_session_by_uid(conn, uid)
                if existing:
                    u = {"sid": existing[0], "complete": existing[1]}
            if not u:
                # Never saw start or any data for this uid — ack zero so the ESP32
                # keeps the file and retries (rather than deleting unstored data).
                _publish_ack(client, uid, 0, 0)
                print(f"[session] STOP uid={uid} → unknown, ack 0/0")
                return
            sid = u["sid"]
            if not u["complete"]:
                conn.execute("UPDATE sessions SET ended=? WHERE id=?", (received, sid))
                conn.commit()
                u["complete"] = True
                uploads[uid] = u
            hr  = conn.execute("SELECT COUNT(*) FROM hr  WHERE session=?", (sid,)).fetchone()[0]
            acc = conn.execute("SELECT COUNT(*) FROM acc WHERE session=?", (sid,)).fetchone()[0]
            _publish_ack(client, uid, hr, acc)
            current_session_id = None
            print(f"[session] STOP uid={uid} id={sid} → ack hr={hr} acc={acc}")


def _handle_session_legacy(data, received, action):
    """Pre-uid firmware path: one global open session, no ack."""
    global current_session_id
    with sqlite3.connect(DB_PATH) as conn:
        if action == "start":
            label = (data.get("label") or "").strip() or None
            started = _epoch_to_started(data.get("started_epoch"), received)
            cur = conn.execute("INSERT INTO sessions (started, label, kind) VALUES (?, ?, ?)",
                               (started, label, pending_kind))
            conn.commit()
            current_session_id = cur.lastrowid
            print(f"[session] START (legacy) → id={current_session_id} at {started}")
        elif action == "stop":
            if current_session_id is not None:
                conn.execute("UPDATE sessions SET ended=? WHERE id=?",
                             (received, current_session_id))
                conn.commit()
                print(f"[session] STOP (legacy) → id={current_session_id}")
            current_session_id = None


def handle_hr(data, received, ts):
    global last_write_iso
    readings = data.get("readings", [])   # "readings" here is the MQTT payload key, not the table
    uid      = data.get("uid")
    with sqlite3.connect(DB_PATH) as conn:
        sid = _sid_for_data(conn, uid, received)
        if sid is None and uid is not None:
            print(f"[{ts}] HR batch uid={uid} — session already complete, ignoring {len(readings)}")
            return
        print(f"[{ts}] HR batch — {len(readings)} reading(s) → session {sid}:")
        for r in readings:
            bpm    = r["bpm"]
            t_ms   = r.get("t_ms", 0)
            rr     = r.get("rr_ms", [])
            rr_str = json.dumps([round(x) for x in rr]) if rr else None
            conn.execute(
                "INSERT INTO hr (received, t_ms, bpm, rr_ms, session) VALUES (?, ?, ?, ?, ?)",
                (received, t_ms, bpm, rr_str, sid),
            )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM hr").fetchone()[0]
    last_write_iso = received
    print(f"  → saved to {DB_PATH.name}  (hr rows: {total})")


def handle_acc(data, received, ts):
    global last_write_iso
    # Samples are compact arrays: [t_ms, x, y, z]
    samples = data.get("samples", [])
    rate    = data.get("sample_rate_hz", "?")
    uid     = data.get("uid")
    with sqlite3.connect(DB_PATH) as conn:
        sid = _sid_for_data(conn, uid, received)
        if sid is None and uid is not None:
            print(f"[{ts}] ACC batch uid={uid} — session already complete, ignoring {len(samples)}")
            return
        print(f"[{ts}] ACC batch — {len(samples)} sample(s) @ {rate} Hz → session {sid}")
        conn.executemany(
            "INSERT INTO acc (received, t_ms, x, y, z, session) VALUES (?, ?, ?, ?, ?, ?)",
            [(received, s[0], s[1], s[2], s[3], sid) for s in samples if len(s) == 4],
        )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM acc").fetchone()[0]
    if samples:
        last_write_iso = received
    if samples:
        last = samples[-1]
        print(f"  last: x={last[1]} y={last[2]} z={last[3]} mg  → {DB_PATH.name} (acc rows: {total})")


def on_message(client, userdata, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        data = json.loads(msg.payload)
    except json.JSONDecodeError:
        print(f"[{ts}] Bad JSON on {msg.topic}, skipping")
        return

    received = datetime.now().isoformat(timespec="seconds")
    if msg.topic == MQTT_TOPIC_CMD:
        handle_cmd(data)
    elif msg.topic == MQTT_TOPIC_SESSION:
        handle_session(client, data, received)
    elif msg.topic == MQTT_TOPIC_ACC:
        handle_acc(data, received, ts)
    else:
        handle_hr(data, received, ts)


def _row_count(table):
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def heartbeat_loop(client):
    """Publish a retained pi/status every HEARTBEAT_S so the ESP32 (and anything
    else) can see the receiver is alive and how much data has landed."""
    while True:
        payload = json.dumps({
            "receiver_ok": True,
            "last_write":  last_write_iso,
            "hr_rows":     _row_count("hr"),
            "acc_rows":    _row_count("acc"),
            "session":     current_session_id,
        })
        client.publish(MQTT_TOPIC_PI, payload, retain=True)
        time.sleep(HEARTBEAT_S)


def main():
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

    if "YOUR-CLUSTER" in MQTT_HOST or "YOUR_MQTT" in MQTT_USER:
        print("ERROR: set MQTT_HOST / MQTT_USER / MQTT_PASS env vars (see the top of this file).")
        sys.exit(1)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="rpi-server")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)   # HiveMQ Cloud requires TLS
    # Last Will: if this process drops, the broker flips the heartbeat to offline
    # so the ESP32 stops showing a stale "alive" almost immediately.
    client.will_set(MQTT_TOPIC_PI,
                    json.dumps({"receiver_ok": False, "last_write": None}),
                    retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"Saving data to: {DB_PATH}")
    print(f"Connecting to HiveMQ Cloud at {MQTT_HOST}:{MQTT_PORT}...\n")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    threading.Thread(target=heartbeat_loop, args=(client,), daemon=True).start()
    client.loop_forever()   # auto-reconnects on drop


if __name__ == "__main__":
    main()
