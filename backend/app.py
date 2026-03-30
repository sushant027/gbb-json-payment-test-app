"""Flask application factory."""
import logging
import os
from flask import Flask
from backend.config import ensure_dirs
from backend.db import init_db

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def create_app():
    """Create and configure the Flask application."""
    logger.info("Creating Flask application")

    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend"),
        static_url_path=""
    )
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB upload limit

    # Ensure data directories exist
    ensure_dirs()

    # Initialize database
    init_db()

    # Register blueprints
    from backend.routes.schemes import schemes_bp
    from backend.routes.test_runs import test_runs_bp
    from backend.routes.xml_gen import xml_gen_bp
    from backend.routes.processing import processing_bp
    from backend.routes.results import results_bp

    app.register_blueprint(schemes_bp, url_prefix="/api/schemes")
    app.register_blueprint(test_runs_bp, url_prefix="/api/test-runs")
    app.register_blueprint(xml_gen_bp, url_prefix="/api/xml")
    app.register_blueprint(processing_bp, url_prefix="/api/processing")
    app.register_blueprint(results_bp, url_prefix="/api/results")

    # Serve frontend
    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    logger.info("Flask application created successfully")
    return app
