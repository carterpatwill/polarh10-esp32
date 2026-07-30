"""Paths and tunables shared across the API package.

Everything the dashboard reads from the environment is resolved here once, so the
blueprints can just import constants. Paths are anchored to the dashboard folder
(this file's grandparent) so they hold whether you run locally or on the Pi.
"""
import os
from pathlib import Path

# .../dashboard/api/config.py → .../dashboard
DASHBOARD_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DASHBOARD_DIR.parent.parent          # .../esp32-polar

# The receiver's DB sits in a sibling folder by default (see deploy-dashboard.sh).
DEFAULT_DB = DASHBOARD_DIR.parent / "server" / "hr_data.db"
DB_PATH = Path(os.environ.get("HR_DB", DEFAULT_DB))
PORT = int(os.environ.get("PORT", "8000"))

# Built React bundle served to the browser. Produced by `npm run build` in web/
# and rsynced to the Pi; never committed (see .gitignore).
DIST_DIR = DASHBOARD_DIR / "static" / "dist"

# Trained activity guesser (data/activity.py). On the Pi the model is shipped
# next to app.py by deploy-dashboard.sh; locally it falls back to the repo copy.
# ACTIVITY_MODEL overrides both.
_SHIPPED_MODEL = DASHBOARD_DIR / "activity_model.joblib"
_REPO_MODEL = REPO_ROOT / "data" / "labeled_data" / "activity_model.joblib"
DEFAULT_MODEL = _SHIPPED_MODEL if _SHIPPED_MODEL.exists() else _REPO_MODEL
MODEL_PATH = Path(os.environ.get("ACTIVITY_MODEL", DEFAULT_MODEL))

# Self-service labeler lives in the repo's data/ dir (Mac-only; not deployed to
# the Pi). LABELER_DATA overrides where we look for library_api.py.
DATA_DIR = Path(os.environ.get("LABELER_DATA", REPO_ROOT / "data"))

# Cap points sent to the browser so long sessions stay smooth on a phone / 1GB Pi.
ACC_TARGET = 4000
HR_TARGET = 3000

# The H10 stamps a whole BATCH of accelerometer samples with one t_ms, so per-sample
# time from t_ms is useless (many samples share an x → vertical stripes on the chart).
# ACC streams at a known fixed rate, so we place samples evenly by index instead.
ACC_SAMPLE_RATE_HZ = 25          # must match ACC_SAMPLE_RATE in esp32/src/config.h
