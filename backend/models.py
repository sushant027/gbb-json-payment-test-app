"""Database model functions for schemes, test_runs, and transactions."""
import json
import logging
from backend.db import get_db

logger = logging.getLogger(__name__)


def _row_to_dict(row):
    """Convert a sqlite3.Row to a dict."""
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows):
    """Convert a list of sqlite3.Row to list of dicts."""
    return [dict(r) for r in rows]


# ── Schemes ──────────────────────────────────────────────────────────────

def create_scheme(scheme_name, is_response_xml_split='N'):
    logger.info("Creating scheme: %s (is_response_xml_split=%s)", scheme_name, is_response_xml_split)
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO schemes (scheme_name, is_response_xml_split) VALUES (?, ?)",
            (scheme_name, is_response_xml_split)
        )
        db.commit()
        scheme_id = cur.lastrowid
        logger.info("Scheme created with id=%d (is_response_xml_split=%s)", scheme_id, is_response_xml_split)
        return scheme_id
    finally:
        db.close()


def get_scheme(scheme_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM schemes WHERE id = ?", (scheme_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        db.close()


def get_all_schemes():
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM schemes ORDER BY created_at DESC").fetchall()
        return _rows_to_list(rows)
    finally:
        db.close()


def update_scheme_mapping(scheme_id, mapping_config):
    logger.info("Updating mapping config for scheme id=%d", scheme_id)
    db = get_db()
    try:
        db.execute(
            "UPDATE schemes SET mapping_config = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(mapping_config), scheme_id)
        )
        db.commit()
        logger.info("Mapping config updated for scheme id=%d", scheme_id)
    finally:
        db.close()


def update_scheme_filename_pattern(scheme_id, filename_pattern):
    """Update the filename_pattern for a scheme."""
    logger.info("Updating filename_pattern for scheme id=%d", scheme_id)
    db = get_db()
    try:
        db.execute(
            "UPDATE schemes SET filename_pattern = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(filename_pattern) if filename_pattern else None, scheme_id)
        )
        db.commit()
    finally:
        db.close()


def update_scheme_xml_template(scheme_id, xml_type, xml_content):
    logger.info("Updating %s XML template for scheme id=%d", xml_type, scheme_id)
    column = f"{xml_type}_xml_template"
    db = get_db()
    try:
        db.execute(
            f"UPDATE schemes SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (xml_content, scheme_id)
        )
        db.commit()
    finally:
        db.close()


def delete_scheme(scheme_id):
    logger.info("Deleting scheme id=%d", scheme_id)
    db = get_db()
    try:
        db.execute("DELETE FROM schemes WHERE id = ?", (scheme_id,))
        db.commit()
    finally:
        db.close()


# ── Test Runs ────────────────────────────────────────────────────────────

def create_test_run(scheme_id, upload_filename, total_transactions):
    logger.info("Creating test run: scheme_id=%d, file=%s, txns=%d",
                scheme_id, upload_filename, total_transactions)
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO test_runs (scheme_id, upload_filename, total_transactions) VALUES (?, ?, ?)",
            (scheme_id, upload_filename, total_transactions)
        )
        db.commit()
        run_id = cur.lastrowid
        logger.info("Test run created with id=%d", run_id)
        return run_id
    finally:
        db.close()


def get_test_run(run_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM test_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        db.close()


def get_all_test_runs():
    db = get_db()
    try:
        rows = db.execute(
            """SELECT tr.*, s.scheme_name
               FROM test_runs tr JOIN schemes s ON tr.scheme_id = s.id
               ORDER BY tr.created_at DESC"""
        ).fetchall()
        return _rows_to_list(rows)
    finally:
        db.close()


def update_test_run_status(run_id, status):
    logger.info("Updating test run id=%d status to '%s'", run_id, status)
    db = get_db()
    try:
        db.execute("UPDATE test_runs SET status = ? WHERE id = ?", (status, run_id))
        db.commit()
    finally:
        db.close()


def update_test_run_report(run_id, report_path):
    db = get_db()
    try:
        db.execute(
            "UPDATE test_runs SET report_path = ?, status = 'completed' WHERE id = ?",
            (report_path, run_id)
        )
        db.commit()
    finally:
        db.close()


# ── Transactions ─────────────────────────────────────────────────────────

def create_transaction(test_run_id, tc_id, debit_account, debit_account_parent, debit_ifsc,
                       debit_amount, credit_count, credit_json, expected_status):
    logger.debug("Creating transaction: tc_id=%s, debit=%s", tc_id, debit_account)
    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO transactions
               (test_run_id, tc_id, debit_account, debit_account_parent, debit_ifsc, debit_amount,
                credit_count, credit_json, expected_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (test_run_id, tc_id, debit_account, debit_account_parent, debit_ifsc, debit_amount,
             credit_count, json.dumps(credit_json), expected_status)
        )
        db.commit()
        txn_id = cur.lastrowid
        logger.debug("Transaction created with id=%d", txn_id)
        return txn_id
    finally:
        db.close()


def get_transactions_by_run(run_id):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM transactions WHERE test_run_id = ? ORDER BY id",
            (run_id,)
        ).fetchall()
        return _rows_to_list(rows)
    finally:
        db.close()


def get_transaction(txn_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        db.close()


def get_transaction_by_batch_ref(batch_reference):
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM transactions WHERE batch_reference = ?",
            (batch_reference,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        db.close()


def update_transaction(txn_id, **kwargs):
    """Update transaction fields dynamically."""
    if not kwargs:
        return
    set_clauses = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [txn_id]
    db = get_db()
    try:
        db.execute(
            f"UPDATE transactions SET {set_clauses} WHERE id = ?", values
        )
        db.commit()
        logger.debug("Transaction id=%d updated: %s", txn_id, list(kwargs.keys()))
    finally:
        db.close()


def update_transaction_credit_json(txn_id, credit_json):
    db = get_db()
    try:
        db.execute(
            "UPDATE transactions SET credit_json = ? WHERE id = ?",
            (json.dumps(credit_json), txn_id)
        )
        db.commit()
    finally:
        db.close()
