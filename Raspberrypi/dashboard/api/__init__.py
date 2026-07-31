"""Flask app factory for the Polar H10 dashboard.

The API is a pure JSON service (blueprints below); the UI is the React app in
web/, built to static/dist/ and served from here. In dev you instead run Vite
(`npm run dev`), which proxies /api to this server — see web/vite.config.js.
"""
from flask import Flask, jsonify, send_from_directory

from . import analysis, labeling, morning, sessions
from .config import DIST_DIR


def create_app():
    # static_folder=None: we serve the built bundle ourselves (below) so a single
    # catch-all can fall back to the app shell for client-side (hash) routes.
    app = Flask(__name__, static_folder=None)

    app.register_blueprint(sessions.bp)
    app.register_blueprint(analysis.bp)
    app.register_blueprint(labeling.bp)
    app.register_blueprint(morning.bp)

    @app.route("/")
    def index():
        return _serve_index()

    @app.route("/<path:path>")
    def spa(path):
        # An /api/* that reached here is an unknown endpoint — keep it a JSON 404
        # instead of quietly returning the HTML shell.
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        target = DIST_DIR / path
        if target.is_file():
            return send_from_directory(str(DIST_DIR), path)
        return _serve_index()          # SPA fallback for client-side routes

    return app


def _serve_index():
    if not (DIST_DIR / "index.html").is_file():
        return (
            "<h1>Dashboard not built</h1>"
            "<p>Run <code>npm install &amp;&amp; npm run build</code> in "
            "<code>web/</code> (or use <code>deploy-dashboard.sh</code>).</p>",
            503,
        )
    return send_from_directory(str(DIST_DIR), "index.html")
