"""Application configuration."""
import os
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA_DIR = os.path.join(BASE_DIR, 'data')

UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
REQUEST_XML_DIR = os.path.join(DATA_DIR, 'xml', 'request')
INITIATION_XML_DIR = os.path.join(DATA_DIR, 'xml', 'initiation')
RESPONSE_XML_DIR = os.path.join(DATA_DIR, 'xml', 'response')
REPORT_DIR = os.path.join(DATA_DIR, 'reports')
GBB_OUTPUT_DIR = os.path.join(DATA_DIR, 'gbb_output')

DB_PATH = os.path.join(DATA_DIR, 'app.db')

ALL_DIRS = [
    DATA_DIR, UPLOAD_DIR, REQUEST_XML_DIR, INITIATION_XML_DIR,
    RESPONSE_XML_DIR, REPORT_DIR, GBB_OUTPUT_DIR,
]


def ensure_dirs():
    """Create all required directories if they don't exist."""
    for d in ALL_DIRS:
        os.makedirs(d, exist_ok=True)
        logger.debug("Ensured directory exists: %s", d)
