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
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

MQTT_HOST      = os.environ.get("MQTT_HOST",  "YOUR-CLUSTER.s1.eu.hivemq.cloud")
MQTT_PORT      = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER      = os.environ.get("MQTT_USER",  "YOUR_MQTT_USERNAME")
MQTT_PASS      = os.environ.get("MQTT_PASS",  "YOUR_MQTT_PASSWORD")
MQTT_TOPIC     = os.environ.get("MQTT_TOPIC",     "polar/hr")
MQTT_TOPIC_ACC = os.environ.get("MQTT_TOPIC_ACC", "polar/acc")

DB_PATH = Path(__file__).parent / "hr_data.db"


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
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
    conn.commit()


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"Connected to {MQTT_HOST}:{MQTT_PORT} — subscribing to "
              f"'{MQTT_TOPIC}' and '{MQTT_TOPIC_ACC}'")
        client.subscribe(MQTT_TOPIC)
        client.subscribe(MQTT_TOPIC_ACC)
    else:
        print(f"Connection failed: {reason_code}")


def handle_hr(data, received, ts):
    readings = data.get("readings", [])
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
                "INSERT INTO readings (received, t_ms, bpm, rr_ms) VALUES (?, ?, ?, ?)",
                (received, t_ms, bpm, rr_str),
            )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    print(f"  → saved to {DB_PATH.name}  (readings rows: {total})")


def handle_acc(data, received, ts):
    # Samples are compact arrays: [t_ms, x, y, z]
    samples = data.get("samples", [])
    rate    = data.get("sample_rate_hz", "?")
    print(f"[{ts}] ACC batch — {len(samples)} sample(s) @ {rate} Hz")
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT INTO acc (received, t_ms, x, y, z) VALUES (?, ?, ?, ?, ?)",
            [(received, s[0], s[1], s[2], s[3]) for s in samples if len(s) == 4],
        )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM acc").fetchone()[0]
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
    if msg.topic == MQTT_TOPIC_ACC:
        handle_acc(data, received, ts)
    else:
        handle_hr(data, received, ts)


def main():
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

    if "YOUR-CLUSTER" in MQTT_HOST or "YOUR_MQTT" in MQTT_USER:
        print("ERROR: set MQTT_HOST / MQTT_USER / MQTT_PASS env vars (see the top of this file).")
        sys.exit(1)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="rpi-hr-receiver")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)   # HiveMQ Cloud requires TLS
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"Saving data to: {DB_PATH}")
    print(f"Connecting to HiveMQ Cloud at {MQTT_HOST}:{MQTT_PORT}...\n")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()   # auto-reconnects on drop


if __name__ == "__main__":
    main()
