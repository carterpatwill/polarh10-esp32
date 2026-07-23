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
MQTT_TOPIC_SESSION = os.environ.get("MQTT_TOPIC_SESSION", "polar/session") # start/stop we receive

HEARTBEAT_S = 5   # publish pi/status this often

DB_PATH = Path(__file__).parent / "hr_data.db"

# Live state shared with the heartbeat thread.
current_session_id = None            # id of the open session, or None when idle
last_write_iso     = None            # ISO time of the most recent DB insert


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
            label    TEXT                 -- user-supplied name for the session, or NULL
        )
    """)
    # Tag data rows with the session they belong to. ADD COLUMN is a no-op error
    # on DBs that already have it, so ignore that specific failure.
    for table in ("hr", "acc"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN session INTEGER")
        except sqlite3.OperationalError:
            pass
    # Backfill the label column on DBs created before labels existed.
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN label TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"Connected to {MQTT_HOST}:{MQTT_PORT} — subscribing to "
              f"'{MQTT_TOPIC}', '{MQTT_TOPIC_ACC}', '{MQTT_TOPIC_SESSION}'")
        client.subscribe(MQTT_TOPIC)
        client.subscribe(MQTT_TOPIC_ACC)
        client.subscribe(MQTT_TOPIC_SESSION)
    else:
        print(f"Connection failed: {reason_code}")


def handle_session(data, received):
    """Open/close a session row and set the id we tag incoming data with."""
    global current_session_id
    action = data.get("action")
    with sqlite3.connect(DB_PATH) as conn:
        if action == "start":
            label = (data.get("label") or "").strip() or None
            cur = conn.execute("INSERT INTO sessions (started, label) VALUES (?, ?)",
                               (received, label))
            conn.commit()
            current_session_id = cur.lastrowid
            print(f"[session] START → id={current_session_id} at {received}"
                  + (f"  label={label!r}" if label else ""))
        elif action == "stop":
            if current_session_id is not None:
                conn.execute("UPDATE sessions SET ended=? WHERE id=?",
                             (received, current_session_id))
                conn.commit()
                print(f"[session] STOP  → id={current_session_id} at {received}")
            current_session_id = None


def handle_hr(data, received, ts):
    global last_write_iso
    readings = data.get("readings", [])   # "readings" here is the MQTT payload key, not the table
    print(f"[{ts}] HR batch — {len(readings)} reading(s):")
    with sqlite3.connect(DB_PATH) as conn:
        for r in readings:
            bpm    = r["bpm"]
            t_ms   = r.get("t_ms", 0)
            rr     = r.get("rr_ms", [])
            rr_str = json.dumps([round(x) for x in rr]) if rr else None

            t_sec  = t_ms / 1000.0
            print(f"  t={t_sec:.1f}s  {bpm} BPM" + (f"  RR: {rr_str} ms" if rr_str else ""))

            conn.execute(
                "INSERT INTO hr (received, t_ms, bpm, rr_ms, session) VALUES (?, ?, ?, ?, ?)",
                (received, t_ms, bpm, rr_str, current_session_id),
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
    print(f"[{ts}] ACC batch — {len(samples)} sample(s) @ {rate} Hz")
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT INTO acc (received, t_ms, x, y, z, session) VALUES (?, ?, ?, ?, ?, ?)",
            [(received, s[0], s[1], s[2], s[3], current_session_id) for s in samples if len(s) == 4],
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
    if msg.topic == MQTT_TOPIC_SESSION:
        handle_session(data, received)
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
