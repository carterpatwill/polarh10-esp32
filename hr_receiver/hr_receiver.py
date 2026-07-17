#!/usr/bin/env python3
"""Receives HR batches from the ESP32, prints them, and saves to hr_data.db (SQLite)."""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import socket
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from zeroconf import ServiceInfo, Zeroconf

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


esp_seen = False

class HRHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global esp_seen
        ts = datetime.now().strftime("%H:%M:%S")
        client_ip = self.client_address[0]

        if self.path == "/hello":
            esp_seen = True
            print(f"[{ts}] ESP32 connected from {client_ip} — waiting for batches...")
            self._respond(200, {"ok": True})
            return

        if self.path != "/hr":
            self.send_response(404)
            self.end_headers()
            return

        print(f"[{ts}] Receiving batch...")

        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, {"error": "bad json"})
            return

        readings = data.get("readings", [])
        received = datetime.now().isoformat(timespec="seconds")

        print(f"[{ts}] Batch — {len(readings)} reading(s):")
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


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

    local_ip = get_local_ip()

    # Advertise as hr-server.local via mDNS so ESP32 can find us automatically
    zc = Zeroconf()
    info = ServiceInfo(
        "_http._tcp.local.",
        "hr-server._http._tcp.local.",
        addresses=[socket.inet_aton(local_ip)],
        port=PORT,
        properties={},
        server="hr-server.local.",
    )
    zc.register_service(info, cooperating_responders=True)

    print(f"HR receiver listening on {local_ip}:{PORT}")
    print(f"Advertising as hr-server.local (mDNS) — no IP config needed on ESP32")
    print(f"Saving data to: {DB_PATH}\n")
    print("Waiting for connection from ESP32...\n")

    try:
        server = HTTPServer(("0.0.0.0", PORT), HRHandler)
        server.allow_reuse_address = True
        server.serve_forever()
    finally:
        zc.unregister_service(info)
        zc.close()
