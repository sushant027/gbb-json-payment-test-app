"""Application configuration."""
import os
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA_DIR = os.path.join(BASE_DIR, 'data')

UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
REQUEST_JSON_DIR = os.path.join(DATA_DIR, 'json', 'request')
INITIATION_JSON_DIR = os.path.join(DATA_DIR, 'json', 'initiation')
RESPONSE_JSON_DIR = os.path.join(DATA_DIR, 'json', 'response')
REPORT_DIR = os.path.join(DATA_DIR, 'reports')

GBB_ENDPOINT_URL = os.environ.get('GBB_ENDPOINT_URL', 'http://localhost:8080/api/payments')

DB_PATH = os.path.join(DATA_DIR, 'app.db')

ALL_DIRS = [
    DATA_DIR, UPLOAD_DIR, REQUEST_JSON_DIR, INITIATION_JSON_DIR,
    RESPONSE_JSON_DIR, REPORT_DIR,
]


def ensure_dirs():
    """Create all required directories if they don't exist."""
    for d in ALL_DIRS:
        os.makedirs(d, exist_ok=True)
        logger.debug("Ensured directory exists: %s", d)
