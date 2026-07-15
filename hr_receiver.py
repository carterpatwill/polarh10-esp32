#!/usr/bin/env python3
"""Receives HR batches from the ESP32, prints them, and saves to hr_data.db (SQLite)."""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import socket
import sqlite3
from datetime import datetime
from pathlib import Path

PORT = 8765
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
    conn.commit()


class HRHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/hr":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, {"error": "bad json"})
            return

        readings = data.get("readings", [])
        received = datetime.now().isoformat(timespec="seconds")
        ts = datetime.now().strftime("%H:%M:%S")

        print(f"\n[{ts}] Batch — {len(readings)} reading(s):")
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

        total = sqlite3.connect(DB_PATH).execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        print(f"  → saved to {DB_PATH.name}  (total rows: {total})")

        self._respond(200, {"ok": True, "count": len(readings)})

    def _respond(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "127.0.0.1"

    print(f"HR receiver listening on port {PORT}")
    print(f"Set SERVER_URL in src/config.h to:  http://{local_ip}:{PORT}/hr")
    print(f"Saving data to: {DB_PATH}\n")
    print("Waiting for batches from the ESP32...\n")

    HTTPServer(("0.0.0.0", PORT), HRHandler).serve_forever()
