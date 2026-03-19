"""Flask application factory."""

from flask import Flask, send_file, send_from_directory

from .helpers import BASE
from .routes import api
from .poller import start_poller
from .tracker import tracker


def create_app():
    app = Flask(__name__, static_folder=None)

    # -- CORS --
    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    # -- Frontend --
    @app.route("/")
    def index():
        return send_file(BASE / "public" / "index.html")

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(BASE / "static", filename)

    @app.route("/src/<path:filename>")
    def src_files(filename):
        return send_from_directory(BASE / "src", filename)

    app.register_blueprint(api)

    # Start background services
    start_poller()
    tracker.start()

    return app
