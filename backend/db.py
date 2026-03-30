"""SQLite database connection and schema initialization."""
import sqlite3
import logging
from backend.config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schemes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_name TEXT UNIQUE NOT NULL,
    request_xml_template TEXT,
    initiation_xml_template TEXT,
    response_xml_template TEXT,
    is_response_xml_split TEXT DEFAULT 'N',
    response_fail_xml_template TEXT,
    mapping_config TEXT,
    filename_pattern TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id INTEGER NOT NULL,
    upload_filename TEXT NOT NULL,
    total_transactions INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending',
    report_path TEXT,
    FOREIGN KEY (scheme_id) REFERENCES schemes(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_run_id INTEGER NOT NULL,
    tc_id TEXT,
    batch_reference TEXT UNIQUE,
    debit_account TEXT NOT NULL,
    debit_account_parent TEXT DEFAULT '',
    debit_ifsc TEXT,
    debit_amount REAL NOT NULL,
    credit_count INTEGER DEFAULT 0,
    credit_json TEXT,
    expected_status TEXT,
    actual_debit_status TEXT,
    actual_debit_remarks TEXT,
    validation_result TEXT,
    initiation_validation TEXT DEFAULT '',
    initiation_validation_desc TEXT DEFAULT '',
    response_validation TEXT DEFAULT '',
    response_validation_desc TEXT DEFAULT '',
    request_xml_path TEXT,
    initiation_xml_path TEXT,
    response_xml_path TEXT,
    debit_fields_initiation TEXT DEFAULT '',
    debit_fields_response TEXT DEFAULT '',
    credit_fields_initiation TEXT DEFAULT '',
    credit_fields_response TEXT DEFAULT '',
    credit_fields_response_failed TEXT DEFAULT '',
    debit_initiation_status TEXT DEFAULT '',
    debit_initiation_remarks TEXT DEFAULT '',
    generated_xml_filename TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (test_run_id) REFERENCES test_runs(id)
);
"""

MIGRATION_SQL = """
ALTER TABLE transactions ADD COLUMN initiation_validation TEXT DEFAULT '';
ALTER TABLE transactions ADD COLUMN initiation_validation_desc TEXT DEFAULT '';
ALTER TABLE transactions ADD COLUMN response_validation TEXT DEFAULT '';
ALTER TABLE transactions ADD COLUMN response_validation_desc TEXT DEFAULT '';
"""


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize the database schema and run migrations for existing DBs."""
    logger.info("Initializing database at %s", DB_PATH)
    conn = get_db()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        # Run migrations for existing databases (add new columns if missing)
        _run_migrations(conn)
        logger.info("Database schema initialized successfully")
    except Exception:
        logger.exception("Failed to initialize database")
        raise
    finally:
        conn.close()


def _run_migrations(conn):
    """Add new columns to existing tables if they don't exist."""
    # ── Transactions table migrations ─────────────────────────────────
    cursor = conn.execute("PRAGMA table_info(transactions)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    logger.debug("Existing transactions columns: %s", existing_cols)

    new_cols = {
        "initiation_validation": "TEXT DEFAULT ''",
        "initiation_validation_desc": "TEXT DEFAULT ''",
        "response_validation": "TEXT DEFAULT ''",
        "response_validation_desc": "TEXT DEFAULT ''",
        "debit_fields_initiation": "TEXT DEFAULT ''",
        "debit_fields_response": "TEXT DEFAULT ''",
        "credit_fields_initiation": "TEXT DEFAULT ''",
        "credit_fields_response": "TEXT DEFAULT ''",
        "credit_fields_response_failed": "TEXT DEFAULT ''",
        "debit_initiation_status": "TEXT DEFAULT ''",
        "debit_initiation_remarks": "TEXT DEFAULT ''",
        "generated_xml_filename": "TEXT DEFAULT ''",
        "debit_account_parent": "TEXT DEFAULT ''",
    }

    for col_name, col_type in new_cols.items():
        if col_name not in existing_cols:
            logger.info("Adding column '%s' to transactions table", col_name)
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_type}")

    # Migrate schemes table
    cursor = conn.execute("PRAGMA table_info(schemes)")
    existing_scheme_cols = {row[1] for row in cursor.fetchall()}
    if "filename_pattern" not in existing_scheme_cols:
        logger.info("Adding column 'filename_pattern' to schemes table")
        conn.execute("ALTER TABLE schemes ADD COLUMN filename_pattern TEXT")

    conn.commit()
