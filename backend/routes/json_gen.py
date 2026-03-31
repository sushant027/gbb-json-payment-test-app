"""JSON generation routes — generate request JSONs and download."""
import io
import json
import logging
import os
import zipfile
from collections import OrderedDict
from flask import Blueprint, request, jsonify, send_file
from backend import models
from backend.config import REQUEST_JSON_DIR
from backend.services.json_generator import generate_request_json, generate_multi_batch_json

logger = logging.getLogger(__name__)
json_gen_bp = Blueprint("json_gen", __name__)


@json_gen_bp.route("/test-runs/<int:run_id>/generate", methods=["POST"])
def generate_jsons(run_id):
    """Generate request JSON files for all transactions in a test run.

    In multi-batch mode, transactions with the same tc_id are grouped into
    a single JSON file with multiple BatchDetails entries.
    """
    logger.info("POST /api/json/test-runs/%d/generate", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    scheme = models.get_scheme(run["scheme_id"])
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    mapping_config = scheme.get("mapping_config")
    if isinstance(mapping_config, str):
        mapping_config = json.loads(mapping_config)

    if not mapping_config or not mapping_config.get("request"):
        return jsonify({"error": "Scheme has no request mapping configured. Please configure the mapping first."}), 400

    request_config = mapping_config.get("request", {})
    is_multi_batch = request_config.get("is_multi_batch", False)

    # Parse filename_pattern from scheme (stored in its own column)
    filename_pattern = scheme.get("filename_pattern")
    if isinstance(filename_pattern, str):
        filename_pattern = json.loads(filename_pattern)

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found for this test run"}), 404

    # Parse credit_json for all transactions upfront
    for txn in transactions:
        if isinstance(txn.get("credit_json"), str):
            txn["credit_json"] = json.loads(txn["credit_json"])

    logger.info("Generating request JSONs for %d transactions (scheme=%s, multi_batch=%s)",
                len(transactions), scheme.get("scheme_name"), is_multi_batch)

    # Create run-specific output directory
    run_dir = os.path.join(REQUEST_JSON_DIR, f"run_{run_id}")
    os.makedirs(run_dir, exist_ok=True)

    generated = 0
    errors = []

    if is_multi_batch:
        # Group transactions by tc_id (preserve insertion order)
        groups = OrderedDict()
        for txn in transactions:
            tc_id = txn["tc_id"]
            groups.setdefault(tc_id, []).append(txn)

        logger.info("Multi-batch mode: %d tc_id groups from %d transactions",
                     len(groups), len(transactions))

        for tc_id, group_txns in groups.items():
            try:
                filepath, batch_refs, all_credits = generate_multi_batch_json(
                    group_txns, mapping_config, run_dir, filename_pattern=filename_pattern
                )

                generated_filename = os.path.basename(filepath)

                # Update each transaction in the group with its own batch_reference
                for txn, batch_ref, credits in zip(group_txns, batch_refs, all_credits):
                    models.update_transaction(
                        txn["id"],
                        batch_reference=batch_ref,
                        request_xml_path=filepath,
                        generated_xml_filename=generated_filename,
                        status="json_generated"
                    )
                    models.update_transaction_credit_json(txn["id"], credits)

                generated += len(group_txns)
                logger.debug("Generated multi-batch JSON for tc_id=%s: %s (%d batches)",
                             tc_id, filepath, len(group_txns))

            except Exception as e:
                logger.exception("Failed to generate multi-batch JSON for tc_id=%s", tc_id)
                for txn in group_txns:
                    errors.append({"transaction_id": txn["id"], "tc_id": txn["tc_id"], "error": str(e)})
    else:
        # Single-batch mode: one JSON per transaction
        for txn in transactions:
            try:
                filepath, batch_ref, updated_credits = generate_request_json(
                    txn, mapping_config, run_dir, filename_pattern=filename_pattern
                )

                generated_filename = os.path.basename(filepath)
                models.update_transaction(
                    txn["id"],
                    batch_reference=batch_ref,
                    request_xml_path=filepath,
                    generated_xml_filename=generated_filename,
                    status="json_generated"
                )
                models.update_transaction_credit_json(txn["id"], updated_credits)

                generated += 1
                logger.debug("Generated JSON for tc_id=%s: %s", txn["tc_id"], filepath)

            except Exception as e:
                logger.exception("Failed to generate JSON for transaction id=%d", txn["id"])
                errors.append({"transaction_id": txn["id"], "tc_id": txn["tc_id"], "error": str(e)})

    # Update test run status
    if generated > 0:
        models.update_test_run_status(run_id, "json_generated")

    result = {
        "generated": generated,
        "errors": len(errors),
        "total": len(transactions),
        "multi_batch": is_multi_batch,
        "output_dir": run_dir,
        "message": f"Generated {generated}/{len(transactions)} request JSON files"
    }
    if errors:
        result["error_details"] = errors

    logger.info("JSON generation complete: %d generated, %d errors", generated, len(errors))
    return jsonify(result)


@json_gen_bp.route("/test-runs/<int:run_id>/download", methods=["GET"])
def download_jsons(run_id):
    """Download all generated request JSONs as a zip file."""
    logger.info("GET /api/json/test-runs/%d/download", run_id)

    run_dir = os.path.join(REQUEST_JSON_DIR, f"run_{run_id}")
    if not os.path.isdir(run_dir):
        return jsonify({"error": "No generated JSONs found for this test run"}), 404

    json_files = [f for f in os.listdir(run_dir) if f.endswith(".json")]
    if not json_files:
        return jsonify({"error": "No JSON files found"}), 404

    logger.info("Creating zip with %d JSON files", len(json_files))

    # Create zip in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in json_files:
            filepath = os.path.join(run_dir, filename)
            zf.write(filepath, filename)

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"request_jsons_run_{run_id}.zip"
    )
