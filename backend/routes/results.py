"""Validation and report routes — run validation, view results, generate/download reports."""
import json
import logging
import os
from flask import Blueprint, jsonify, send_file
from backend import models
from backend.config import REPORT_DIR
from backend.services.validator import validate_transaction
from backend.services.json_parser import parse_json_file
from backend.services.report_generator import generate_report

logger = logging.getLogger(__name__)
results_bp = Blueprint("results", __name__)


@results_bp.route("/test-runs/<int:run_id>/validate", methods=["POST"])
def validate_run(run_id):
    """Run validation for all transactions in a test run."""
    logger.info("POST /api/results/test-runs/%d/validate", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    scheme = models.get_scheme(run["scheme_id"])
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    mapping_config = scheme.get("mapping_config")
    if isinstance(mapping_config, str):
        mapping_config = json.loads(mapping_config)

    if not mapping_config:
        return jsonify({"error": "Scheme has no mapping configured"}), 400

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found"}), 404

    logger.info("Validating %d transactions for run %d", len(transactions), run_id)

    validated = 0
    passed = 0
    failed = 0
    errors = []

    for txn in transactions:
        try:
            # Read stored parsed data from initiation processing
            initiation_data = None
            if txn.get("debit_fields_initiation"):
                initiation_data = {
                    "batch_reference": txn.get("batch_reference", ""),
                    "debit": json.loads(txn["debit_fields_initiation"]),
                    "credits": json.loads(txn.get("credit_fields_initiation") or "[]"),
                }
                logger.debug("Loaded stored initiation data for tc_id=%s", txn["tc_id"])
            elif txn.get("initiation_xml_path") and os.path.isfile(txn["initiation_xml_path"]):
                # Fallback: re-parse if stored data not available
                initiation_data = parse_json_file(
                    txn["initiation_xml_path"], "initiation", mapping_config
                )
                logger.debug("Parsed initiation JSON for tc_id=%s (fallback)", txn["tc_id"])

            # Read stored parsed data from response processing
            response_data = None
            if txn.get("debit_fields_response"):
                response_data = {
                    "batch_reference": txn.get("batch_reference", ""),
                    "debit": json.loads(txn["debit_fields_response"]),
                    "credits": json.loads(txn.get("credit_fields_response") or "[]"),
                }
                logger.debug("Loaded stored response data for tc_id=%s", txn["tc_id"])
            elif txn.get("response_xml_path") and os.path.isfile(txn["response_xml_path"]):
                # Fallback: re-parse if stored data not available
                response_data = parse_json_file(
                    txn["response_xml_path"], "response", mapping_config
                )
                logger.debug("Parsed response JSON for tc_id=%s (fallback)", txn["tc_id"])

            # Read stored parsed data from failure response processing (split mode)
            response_fail_data = None
            if txn.get("credit_fields_response_failed"):
                logger.debug("Loading failure response data for tc_id=%s", txn["tc_id"])
                response_fail_data = {
                    "batch_reference": txn.get("batch_reference", ""),
                    "debit": {},
                    "credits": json.loads(txn.get("credit_fields_response_failed") or "[]"),
                }
                logger.debug("Loaded %d failure response credits for tc_id=%s",
                             len(response_fail_data["credits"]), txn["tc_id"])

            if not initiation_data and not response_data and not response_fail_data:
                logger.warning("No initiation, response, or failure response data for tc_id=%s "
                               "— skipping validation", txn["tc_id"])
                errors.append({
                    "tc_id": txn["tc_id"],
                    "error": "No initiation or response JSON available"
                })
                continue

            # Run validation
            logger.debug("Running validation for tc_id=%s: initiation=%s, response=%s, "
                         "response_fail=%s", txn["tc_id"],
                         "yes" if initiation_data else "no",
                         "yes" if response_data else "no",
                         "yes" if response_fail_data else "no")
            result = validate_transaction(txn, initiation_data, response_data, mapping_config,
                                          response_fail_data=response_fail_data)

            # Update transaction with validation results + descriptions
            models.update_transaction(
                txn["id"],
                actual_debit_status=result["actual_debit_status"],
                actual_debit_remarks=result["actual_debit_remarks"],
                initiation_validation=result.get("initiation_validation", ""),
                initiation_validation_desc=result.get("initiation_validation_desc", ""),
                response_validation=result.get("response_validation", ""),
                response_validation_desc=result.get("response_validation_desc", ""),
                validation_result=json.dumps({
                    "overall": result["overall"],
                    "debit_validation": result["debit_validation"],
                    "credit_validations": result["credit_validations"]
                }),
                status="validated"
            )

            # Update credit_json with validation results
            models.update_transaction_credit_json(txn["id"], result["updated_credits"])

            validated += 1
            if result["overall"] == "PASS":
                passed += 1
            else:
                failed += 1

            logger.info("Validated tc_id=%s: %s", txn["tc_id"], result["overall"])

        except Exception as e:
            logger.exception("Validation failed for transaction id=%d", txn["id"])
            errors.append({"tc_id": txn.get("tc_id"), "error": str(e)})

    # Update test run status
    models.update_test_run_status(run_id, "validated")

    result = {
        "validated": validated,
        "passed": passed,
        "failed": failed,
        "errors": len(errors),
        "total": len(transactions),
        "message": f"Validated {validated}/{len(transactions)} — {passed} PASS, {failed} FAIL"
    }
    if errors:
        result["error_details"] = errors

    logger.info("Validation complete for run %d: %d validated, %d passed, %d failed",
                run_id, validated, passed, failed)
    return jsonify(result)


@results_bp.route("/test-runs/<int:run_id>/results", methods=["GET"])
def get_results(run_id):
    """Get validation results for a test run."""
    logger.info("GET /api/results/test-runs/%d/results", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    transactions = models.get_transactions_by_run(run_id)

    # Parse JSON fields
    for txn in transactions:
        for field in ("credit_json", "validation_result"):
            if txn.get(field) and isinstance(txn[field], str):
                try:
                    txn[field] = json.loads(txn[field])
                except (json.JSONDecodeError, TypeError):
                    pass

    # Summary stats
    total = len(transactions)
    passed = sum(1 for t in transactions
                 if isinstance(t.get("validation_result"), dict)
                 and t["validation_result"].get("overall") == "PASS")
    failed = sum(1 for t in transactions
                 if isinstance(t.get("validation_result"), dict)
                 and t["validation_result"].get("overall") == "FAIL")

    return jsonify({
        "test_run": run,
        "transactions": transactions,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pending": total - passed - failed
        }
    })


@results_bp.route("/test-runs/<int:run_id>/report", methods=["POST"])
def create_report(run_id):
    """Generate an XLSX validation report."""
    logger.info("POST /api/results/test-runs/%d/report", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    # Get scheme name for the report
    scheme = models.get_scheme(run["scheme_id"])
    if scheme:
        run["scheme_name"] = scheme.get("scheme_name", "")

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found"}), 404

    try:
        report_path = generate_report(run, transactions, REPORT_DIR)
        models.update_test_run_report(run_id, report_path)

        logger.info("Report generated for run %d: %s", run_id, report_path)
        return jsonify({
            "report_path": report_path,
            "message": "Report generated successfully"
        })
    except Exception as e:
        logger.exception("Failed to generate report for run %d", run_id)
        return jsonify({"error": f"Report generation failed: {str(e)}"}), 500


@results_bp.route("/test-runs/<int:run_id>/download-report", methods=["GET"])
def download_report(run_id):
    """Download the generated XLSX report."""
    logger.info("GET /api/results/test-runs/%d/download-report", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    report_path = run.get("report_path")
    if not report_path or not os.path.isfile(report_path):
        return jsonify({"error": "Report not found. Generate a report first."}), 404

    return send_file(
        report_path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=os.path.basename(report_path)
    )
