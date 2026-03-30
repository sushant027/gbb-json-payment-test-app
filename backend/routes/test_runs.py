"""Test run management routes — upload Excel, list runs, get details."""
import json
import logging
import os
from flask import Blueprint, request, jsonify
from backend import models
from backend.config import UPLOAD_DIR
from backend.services.excel_parser import parse_excel

logger = logging.getLogger(__name__)
test_runs_bp = Blueprint("test_runs", __name__)


@test_runs_bp.route("", methods=["GET"])
def list_test_runs():
    """List all test runs."""
    logger.info("GET /api/test-runs — listing all test runs")
    runs = models.get_all_test_runs()
    logger.debug("Found %d test runs", len(runs))
    return jsonify(runs)


@test_runs_bp.route("/<int:run_id>", methods=["GET"])
def get_test_run(run_id):
    """Get test run details with transactions."""
    logger.info("GET /api/test-runs/%d", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    transactions = models.get_transactions_by_run(run_id)

    # Parse JSON fields in transactions
    for txn in transactions:
        if txn.get("credit_json") and isinstance(txn["credit_json"], str):
            try:
                txn["credit_json"] = json.loads(txn["credit_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        if txn.get("validation_result") and isinstance(txn["validation_result"], str):
            try:
                txn["validation_result"] = json.loads(txn["validation_result"])
            except (json.JSONDecodeError, TypeError):
                pass

    run["transactions"] = transactions
    return jsonify(run)


@test_runs_bp.route("/upload", methods=["POST"])
def upload_test_data():
    """Upload an Excel file and create a test run with transactions.

    Expects multipart form with:
    - file: Excel file (.xlsx)
    - scheme_id: ID of the scheme to use
    """
    logger.info("POST /api/test-runs/upload")

    if "file" not in request.files:
        logger.warning("Upload: no file provided")
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    scheme_id = request.form.get("scheme_id")

    if not scheme_id:
        return jsonify({"error": "scheme_id is required"}), 400

    scheme_id = int(scheme_id)
    scheme = models.get_scheme(scheme_id)
    if not scheme:
        return jsonify({"error": f"Scheme with id {scheme_id} not found"}), 404

    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"error": "File must be an Excel file (.xlsx)"}), 400

    # Save uploaded file
    filename = file.filename
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)
    logger.info("Uploaded file saved: %s", filepath)

    try:
        # Parse Excel
        transactions_data = parse_excel(filepath)
        if not transactions_data:
            return jsonify({"error": "No transactions found in the Excel file"}), 400

        logger.info("Parsed %d transactions from upload", len(transactions_data))

        # Create test run
        run_id = models.create_test_run(scheme_id, filename, len(transactions_data))

        # Create transaction records
        created_count = 0
        for txn_data in transactions_data:
            models.create_transaction(
                test_run_id=run_id,
                tc_id=txn_data["tc_id"],
                debit_account=txn_data["debit_account"],
                debit_account_parent=txn_data.get("debit_account_parent", ""),
                debit_ifsc=txn_data.get("debit_ifsc", ""),
                debit_amount=txn_data["debit_amount"],
                credit_count=txn_data["credit_count"],
                credit_json=txn_data["credits"],
                expected_status=txn_data.get("expected_status", "")
            )
            created_count += 1
            logger.debug("Created transaction %d/%d: tc_id=%s",
                         created_count, len(transactions_data), txn_data["tc_id"])

        logger.info("Test run created: id=%d, transactions=%d", run_id, created_count)

        return jsonify({
            "test_run_id": run_id,
            "transactions_created": created_count,
            "message": f"Test run created with {created_count} transactions"
        }), 201

    except Exception as e:
        logger.exception("Failed to process uploaded Excel file")
        return jsonify({"error": f"Failed to process file: {str(e)}"}), 500
