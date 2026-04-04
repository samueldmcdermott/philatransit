"""Flask application factory with dependency injection."""

from flask import Flask, send_file, send_from_directory

from .helpers import BASE
from .routes import api
from .poller import start_poller
from .core.shapes import load_shapes
from .core.trip import TripManager
from .core.route import build_route_config
from .core.tracker import TripTracker


def _create_provider(provider_name: str):
    """Instantiate a Provider by name."""
    if provider_name == "septa":
        from .provider.septa.provider import SeptaProvider
        from .provider.septa.constants import SHAPE_TRIM
        return SeptaProvider(), SHAPE_TRIM
    raise ValueError(f"Unknown provider: {provider_name}")


def create_app(provider_name="septa"):
    # -- Provider --
    provider, shape_trims = _create_provider(provider_name)

    # -- Shapes --
    shapes = load_shapes(BASE, shape_trims=shape_trims)

    # -- Route config (merge provider routes with terminus data) --
    provider_config = provider.get_route_config()
    route_config = build_route_config(provider_config, shapes.termini)

    # -- Trip manager --
    trip_manager = TripManager(shape_registry=shapes, route_config=route_config)
    detour_detector = provider.get_detour_detector()
    if detour_detector:
        trip_manager.set_detour_detector(detour_detector)
    tunnel_detector = provider.get_tunnel_detector()
    if tunnel_detector:
        tunnel_detector.set_shapes(shapes)

    # -- Background services --
    start_poller(provider, trip_manager)
    tracker = TripTracker()
    tracker.start()

    # -- Flask app --
    app = Flask(__name__, static_folder=None)

    # Store dependencies for route handlers
    app.config['provider'] = provider
    app.config['trip_manager'] = trip_manager
    app.config['tracker'] = tracker
    app.config['route_config'] = route_config

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

    return app
