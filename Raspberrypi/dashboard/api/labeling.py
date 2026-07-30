"""Self-service labeler (LOCAL ONLY).

These endpoints let you grow the training library from the browser: see new
recordings, auto-trim the start/stop ramp, label jog/run/walk, and retrain — all
without the terminal. The logic lives in data/library_api.py (next to the trainer
and the library), found via LABELER_DATA (config.DATA_DIR). This is a Mac-only
workflow — the 1GB Pi is left lean for the receiver, so data/ isn't deployed there
and the import fails gracefully (the /label page shows "unavailable"). The env hook
stays so it CAN run elsewhere if that folder + ML deps are present.
"""
import sys

from flask import Blueprint, jsonify, request

from .config import DATA_DIR

bp = Blueprint("labeling", __name__)


def _labeler():
    """Import data/library_api, or None if it (or its deps) aren't here (e.g. Pi)."""
    if str(DATA_DIR) not in sys.path:
        sys.path.insert(0, str(DATA_DIR))
    try:
        import library_api
        return library_api
    except Exception:
        return None


@bp.route("/api/label/status")
def label_status():
    """Whether the labeler is importable here. The React /label page gates on this
    (replaces the server-rendered `available` flag from the old template)."""
    return jsonify({"available": _labeler() is not None})


@bp.route("/api/label/candidates")
def label_candidates():
    lab = _labeler()
    if lab is None:
        return jsonify({"error": "labeler unavailable (run the dashboard locally on the Mac)"}), 501
    return jsonify(lab.candidates())


@bp.route("/api/label/library")
def label_library():
    lab = _labeler()
    if lab is None:
        return jsonify({"error": "labeler unavailable"}), 501
    return jsonify(lab.library())


@bp.route("/api/label/add", methods=["POST"])
def label_add():
    lab = _labeler()
    if lab is None:
        return jsonify({"error": "labeler unavailable"}), 501
    d = request.get_json(force=True)
    res = lab.add(int(d["id"]), d["bucket"],
                  t0=d.get("t0"), t1=d.get("t1"), note=d.get("note", ""))
    return jsonify(res), (400 if "error" in res else 200)


@bp.route("/api/label/remove", methods=["POST"])
def label_remove():
    lab = _labeler()
    if lab is None:
        return jsonify({"error": "labeler unavailable"}), 501
    d = request.get_json(force=True)
    res = lab.remove(int(d["id"]))
    return jsonify(res), (400 if "error" in res else 200)


@bp.route("/api/label/train", methods=["POST"])
def label_train():
    lab = _labeler()
    if lab is None:
        return jsonify({"error": "labeler unavailable"}), 501
    res = lab.train_and_save()
    return jsonify(res), (400 if "error" in res else 200)
