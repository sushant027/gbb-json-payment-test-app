"""XML generation routes — generate request XMLs and download."""
import io
import json
import logging
import os
import zipfile
from flask import Blueprint, request, jsonify, send_file
from backend import models
from backend.config import REQUEST_XML_DIR
from backend.services.xml_generator import generate_request_xml

logger = logging.getLogger(__name__)
xml_gen_bp = Blueprint("xml_gen", __name__)


@xml_gen_bp.route("/test-runs/<int:run_id>/generate", methods=["POST"])
def generate_xmls(run_id):
    """Generate request XML files for all transactions in a test run."""
    logger.info("POST /api/xml/test-runs/%d/generate", run_id)

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

    # Parse filename_pattern from scheme (stored in its own column)
    filename_pattern = scheme.get("filename_pattern")
    if isinstance(filename_pattern, str):
        filename_pattern = json.loads(filename_pattern)

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found for this test run"}), 404

    logger.info("Generating request XMLs for %d transactions (scheme=%s)",
                len(transactions), scheme.get("scheme_name"))

    # Create run-specific output directory
    run_dir = os.path.join(REQUEST_XML_DIR, f"run_{run_id}")
    os.makedirs(run_dir, exist_ok=True)

    generated = 0
    errors = []

    for txn in transactions:
        try:
            # Parse credit_json if needed
            if isinstance(txn.get("credit_json"), str):
                txn["credit_json"] = json.loads(txn["credit_json"])

            filepath, batch_ref, updated_credits = generate_request_xml(
                txn, mapping_config, run_dir, filename_pattern=filename_pattern
            )

            # Update transaction with batch reference, XML path, and generated filename
            generated_filename = os.path.basename(filepath)
            models.update_transaction(
                txn["id"],
                batch_reference=batch_ref,
                request_xml_path=filepath,
                generated_xml_filename=generated_filename,
                status="xml_generated"
            )

            # Update credit_json with generated credit references
            models.update_transaction_credit_json(txn["id"], updated_credits)

            generated += 1
            logger.debug("Generated XML for tc_id=%s: %s", txn["tc_id"], filepath)

        except Exception as e:
            logger.exception("Failed to generate XML for transaction id=%d", txn["id"])
            errors.append({"transaction_id": txn["id"], "tc_id": txn["tc_id"], "error": str(e)})

    # Update test run status
    if generated > 0:
        models.update_test_run_status(run_id, "xml_generated")

    result = {
        "generated": generated,
        "errors": len(errors),
        "total": len(transactions),
        "output_dir": run_dir,
        "message": f"Generated {generated}/{len(transactions)} request XML files"
    }
    if errors:
        result["error_details"] = errors

    logger.info("XML generation complete: %d generated, %d errors", generated, len(errors))
    return jsonify(result)


@xml_gen_bp.route("/test-runs/<int:run_id>/download", methods=["GET"])
def download_xmls(run_id):
    """Download all generated request XMLs as a zip file."""
    logger.info("GET /api/xml/test-runs/%d/download", run_id)

    run_dir = os.path.join(REQUEST_XML_DIR, f"run_{run_id}")
    if not os.path.isdir(run_dir):
        return jsonify({"error": "No generated XMLs found for this test run"}), 404

    xml_files = [f for f in os.listdir(run_dir) if f.endswith(".xml")]
    if not xml_files:
        return jsonify({"error": "No XML files found"}), 404

    logger.info("Creating zip with %d XML files", len(xml_files))

    # Create zip in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in xml_files:
            filepath = os.path.join(run_dir, filename)
            zf.write(filepath, filename)

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"request_xmls_run_{run_id}.zip"
    )
