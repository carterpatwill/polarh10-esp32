#!/usr/bin/env python3
"""Web dashboard for the ESP32 Polar H10 data — entrypoint.

Reads straight from the SAME hr_data.db the receiver writes to (read-only — it
never writes or locks it, except brief renames/deletes). Serves a JSON API plus
the built React app (web/ → static/dist/). Open it from any device on the same
network as the Pi:

    http://pi4server.local:8000

The app itself lives in the `api` package; this file just wires it up and runs it
so the systemd service (WorkingDirectory=this folder) keeps working unchanged.

Config via environment variables:
    HR_DB           path to hr_data.db   (default: ../server/hr_data.db)
    PORT            port to serve on      (default: 8000)
    ACTIVITY_MODEL  path to the model     (default: shipped, else repo copy)
    LABELER_DATA    repo data/ dir        (Mac-only labeler; default: ../../data)

Run manually:
    .venv/bin/python3 app.py
"""
from api import create_app
from api.config import DB_PATH, DIST_DIR, MODEL_PATH, PORT

app = create_app()

if __name__ == "__main__":
    print(f"Reading database: {DB_PATH}  (exists: {DB_PATH.exists()})")
    print(f"Activity model:   {MODEL_PATH}  (exists: {MODEL_PATH.exists()})")
    print(f"React bundle:     {DIST_DIR}  (built: {(DIST_DIR / 'index.html').exists()})")
    print(f"Dashboard on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
