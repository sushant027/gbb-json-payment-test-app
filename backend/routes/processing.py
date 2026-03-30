"""Processing routes — GBB submission, initiation/response file matching."""
import json
import logging
import os
import shutil
from flask import Blueprint, jsonify
from backend import models
from backend.config import (
    REQUEST_XML_DIR, INITIATION_XML_DIR, RESPONSE_XML_DIR, GBB_OUTPUT_DIR
)
from backend.services.file_matcher import find_matching_files, find_matching_files_split
from backend.services.xml_parser import parse_xml_file

logger = logging.getLogger(__name__)
processing_bp = Blueprint("processing", __name__)


@processing_bp.route("/test-runs/<int:run_id>/submit-gbb", methods=["POST"])
def submit_to_gbb(run_id):
    """Copy generated request XMLs to GBB output directory.

    This simulates submitting to GBB by copying files to a configured path.
    """
    logger.info("POST /api/processing/test-runs/%d/submit-gbb", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found"}), 404

    # Create run-specific GBB output directory
    gbb_dir = os.path.join(GBB_OUTPUT_DIR, f"run_{run_id}")
    os.makedirs(gbb_dir, exist_ok=True)

    copied = 0
    for txn in transactions:
        xml_path = txn.get("request_xml_path")
        if xml_path and os.path.isfile(xml_path):
            dest = os.path.join(gbb_dir, os.path.basename(xml_path))
            shutil.copy2(xml_path, dest)
            models.update_transaction(txn["id"], status="initiated")
            copied += 1
            logger.debug("Copied %s to GBB: %s", os.path.basename(xml_path), dest)
        else:
            logger.warning("No request XML found for transaction %d (path=%s)",
                           txn["id"], xml_path)

    if copied > 0:
        models.update_test_run_status(run_id, "initiated")

    logger.info("Submitted %d/%d XMLs to GBB (dir=%s)", copied, len(transactions), gbb_dir)

    return jsonify({
        "submitted": copied,
        "total": len(transactions),
        "gbb_directory": gbb_dir,
        "message": f"Submitted {copied} XML files to GBB"
    })


@processing_bp.route("/test-runs/<int:run_id>/process-initiation", methods=["POST"])
def process_initiation(run_id):
    """Scan initiation folder, match files to transactions by batch reference."""
    logger.info("POST /api/processing/test-runs/%d/process-initiation", run_id)
    return _process_xml_files(run_id, "initiation", INITIATION_XML_DIR)


@processing_bp.route("/test-runs/<int:run_id>/process-response", methods=["POST"])
def process_response(run_id):
    """Scan response folder, match files to transactions by batch reference.

    If the scheme has is_response_xml_split='Y', uses split response processing
    which classifies files as success or failure based on a configured tag.
    """
    logger.info("POST /api/processing/test-runs/%d/process-response", run_id)

    # Check if scheme uses split response XML
    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    scheme = models.get_scheme(run["scheme_id"])
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    is_split = scheme.get("is_response_xml_split", "N") == "Y"
    logger.debug("process_response: run_id=%d, is_response_xml_split=%s",
                 run_id, "Y" if is_split else "N")

    if is_split:
        logger.info("Using split response processing for run %d", run_id)
        return _process_split_response_files(run_id, RESPONSE_XML_DIR)
    else:
        logger.info("Using standard response processing for run %d", run_id)
        return _process_xml_files(run_id, "response", RESPONSE_XML_DIR)


def _process_xml_files(run_id, xml_type, folder_path):
    """Generic processor for initiation/response XML files.

    1. Gets all transactions for the run
    2. Collects their batch_references
    3. Scans the folder for matching XML files
    4. Parses matched files
    5. Updates transactions with extracted data
    """
    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    scheme = models.get_scheme(run["scheme_id"])
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    mapping_config = scheme.get("mapping_config")
    if isinstance(mapping_config, str):
        mapping_config = json.loads(mapping_config)

    if not mapping_config or not mapping_config.get(xml_type):
        return jsonify({
            "error": f"Scheme has no {xml_type} mapping configured"
        }), 400

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found"}), 404

    # Collect batch references
    batch_refs = {}
    for txn in transactions:
        br = txn.get("batch_reference")
        if br:
            batch_refs[br] = txn

    if not batch_refs:
        return jsonify({"error": "No transactions have batch references. Generate XMLs first."}), 400

    logger.info("Looking for %s files matching %d batch references in %s",
                xml_type, len(batch_refs), folder_path)

    # Find matching files
    matched_files = find_matching_files(
        folder_path, xml_type, mapping_config, set(batch_refs.keys())
    )

    # Process matched files
    processed = 0
    errors = []

    for batch_ref, filepaths in matched_files.items():
        txn = batch_refs[batch_ref]
        try:
            # For initiation: single file only (preserve existing behavior)
            # For response: process all matching files to accumulate credits
            if xml_type != "response":
                filepaths = filepaths[:1]

            all_credit_data = []
            all_file_paths = []
            debit_data = {}

            for filepath in filepaths:
                parsed = parse_xml_file(filepath, xml_type, mapping_config)
                file_debit = parsed.get("debit", {})
                file_credits = parsed.get("credits", [])

                # Use debit data from first file (debit should be same across files)
                if not debit_data:
                    debit_data = file_debit

                all_credit_data.extend(file_credits)
                all_file_paths.append(filepath)

                # Enrich credit_json with parsed data from each file
                _update_credits_from_parsed(txn, parsed, xml_type, mapping_config)

            # Update transaction
            path_field = f"{xml_type}_xml_path"
            status_val = f"{xml_type}_matched"

            update_data = {
                path_field: ";".join(all_file_paths),
                "status": status_val,
                f"debit_fields_{xml_type}": json.dumps(debit_data),
                f"credit_fields_{xml_type}": json.dumps(all_credit_data),
            }

            # Extract debit status from parsed data
            if debit_data.get("debit_status"):
                update_data["actual_debit_status"] = debit_data["debit_status"]
            if debit_data.get("debit_remarks"):
                update_data["actual_debit_remarks"] = debit_data["debit_remarks"]

            # Store initiation-specific status in dedicated columns
            if xml_type == "initiation":
                update_data["debit_initiation_status"] = debit_data.get("debit_status", "")
                update_data["debit_initiation_remarks"] = debit_data.get("debit_remarks", "")

            models.update_transaction(txn["id"], **update_data)

            processed += 1
            logger.info("Processed %d %s file(s) for tc_id=%s (batch_ref=%s)",
                        len(filepaths), xml_type, txn["tc_id"], batch_ref)

        except Exception as e:
            logger.exception("Failed to process %s file for batch_ref=%s", xml_type, batch_ref)
            errors.append({"batch_reference": batch_ref, "error": str(e)})

    # Update test run status
    new_status = f"{xml_type}_processed"
    models.update_test_run_status(run_id, new_status)

    total_files = sum(len(fps) for fps in matched_files.values())
    result = {
        "matched": len(matched_files),
        "matched_files": total_files,
        "processed": processed,
        "errors": len(errors),
        "total_transactions": len(transactions),
        "message": f"Processed {processed} {xml_type} batches ({total_files} files matched for {len(matched_files)} batch refs out of {len(batch_refs)} expected)"
    }
    if errors:
        result["error_details"] = errors

    logger.info("%s processing complete: matched=%d batch refs (%d files), processed=%d, errors=%d",
                xml_type, len(matched_files), total_files, processed, len(errors))

    return jsonify(result)


def _process_split_response_files(run_id, folder_path):
    """Process split response XML files — separate success and failure XMLs.

    When a scheme has is_response_xml_split='Y', the response folder contains
    both success and failure XMLs distinguished by a configured indicator tag.
    Success XMLs are parsed with the 'response' mapping and stored in
    credit_fields_response. Failure XMLs are parsed with the 'response_fail'
    mapping and stored in credit_fields_response_failed.
    """
    logger.info("Processing split response files for run %d from %s", run_id, folder_path)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    scheme = models.get_scheme(run["scheme_id"])
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    mapping_config = scheme.get("mapping_config")
    if isinstance(mapping_config, str):
        mapping_config = json.loads(mapping_config)

    if not mapping_config or not mapping_config.get("response"):
        logger.error("Scheme has no response mapping configured")
        return jsonify({"error": "Scheme has no response mapping configured"}), 400

    if not mapping_config.get("response_fail"):
        logger.error("Scheme has no response_fail mapping configured (required for split response)")
        return jsonify({"error": "Scheme has no response_fail mapping configured"}), 400

    success_indicator_tag = mapping_config.get("response", {}).get("success_indicator_tag", "")
    if not success_indicator_tag:
        logger.error("No success_indicator_tag configured in response mapping")
        return jsonify({"error": "No success_indicator_tag configured. "
                        "Configure it in the response mapping."}), 400

    logger.debug("Split response config: success_indicator_tag='%s'", success_indicator_tag)

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found"}), 404

    # Collect batch references
    batch_refs = {}
    for txn in transactions:
        br = txn.get("batch_reference")
        if br:
            batch_refs[br] = txn

    if not batch_refs:
        return jsonify({"error": "No transactions have batch references. Generate XMLs first."}), 400

    logger.info("Looking for split response files matching %d batch references in %s",
                len(batch_refs), folder_path)

    # Find and classify matching files
    success_matches, failure_matches = find_matching_files_split(
        folder_path, mapping_config, set(batch_refs.keys())
    )

    logger.info("Split response file matching: %d success, %d failure",
                len(success_matches), len(failure_matches))

    processed_success = 0
    processed_failure = 0
    errors = []

    # ── Process success response files ────────────────────────────────
    for batch_ref, filepaths in success_matches.items():
        txn = batch_refs[batch_ref]
        try:
            all_credit_data = []
            all_file_paths = []
            debit_data = {}

            for filepath in filepaths:
                logger.debug("Parsing success response XML for batch_ref=%s: %s",
                             batch_ref, filepath)
                parsed = parse_xml_file(filepath, "response", mapping_config)

                file_debit = parsed.get("debit", {})
                file_credits = parsed.get("credits", [])

                if not debit_data:
                    debit_data = file_debit

                all_credit_data.extend(file_credits)
                all_file_paths.append(filepath)
                _update_credits_from_parsed(txn, parsed, "response", mapping_config)

            update_data = {
                "response_xml_path": ";".join(all_file_paths),
                "status": "response_matched",
                "debit_fields_response": json.dumps(debit_data),
                "credit_fields_response": json.dumps(all_credit_data),
            }

            if debit_data.get("debit_status"):
                update_data["actual_debit_status"] = debit_data["debit_status"]
                logger.debug("Success response debit_status=%s for batch_ref=%s",
                             debit_data["debit_status"], batch_ref)
            if debit_data.get("debit_remarks"):
                update_data["actual_debit_remarks"] = debit_data["debit_remarks"]

            models.update_transaction(txn["id"], **update_data)

            processed_success += 1
            logger.info("Processed %d SUCCESS response file(s) for tc_id=%s (batch_ref=%s)",
                        len(filepaths), txn["tc_id"], batch_ref)

        except Exception as e:
            logger.exception("Failed to process success response for batch_ref=%s", batch_ref)
            errors.append({"batch_reference": batch_ref, "type": "success", "error": str(e)})

    # ── Process failure response files ────────────────────────────────
    for batch_ref, filepaths in failure_matches.items():
        txn = batch_refs[batch_ref]
        try:
            all_credit_data = []
            all_file_paths = []

            for filepath in filepaths:
                logger.debug("Parsing failure response XML for batch_ref=%s: %s",
                             batch_ref, filepath)
                parsed = parse_xml_file(filepath, "response_fail", mapping_config)

                all_credit_data.extend(parsed.get("credits", []))
                all_file_paths.append(filepath)
                _update_credits_from_parsed(txn, parsed, "response_fail", mapping_config)

            update_data = {
                "credit_fields_response_failed": json.dumps(all_credit_data),
            }

            # If no success file matched this batch_ref, also set the response_xml_path
            if batch_ref not in success_matches:
                update_data["response_xml_path"] = ";".join(all_file_paths)
                update_data["status"] = "response_matched"
                logger.debug("No success file for batch_ref=%s, using failure file as response_xml_path",
                             batch_ref)

            models.update_transaction(txn["id"], **update_data)

            processed_failure += 1
            logger.info("Processed %d FAILURE response file(s) for tc_id=%s (batch_ref=%s)",
                        len(filepaths), txn["tc_id"], batch_ref)

        except Exception as e:
            logger.exception("Failed to process failure response for batch_ref=%s", batch_ref)
            errors.append({"batch_reference": batch_ref, "type": "failure", "error": str(e)})

    # Update test run status
    models.update_test_run_status(run_id, "response_processed")

    total_processed = processed_success + processed_failure
    total_matched_refs = len(success_matches) + len(failure_matches)
    total_success_files = sum(len(fps) for fps in success_matches.values())
    total_failure_files = sum(len(fps) for fps in failure_matches.values())

    result = {
        "matched": total_matched_refs,
        "matched_files": total_success_files + total_failure_files,
        "processed": total_processed,
        "success_files": total_success_files,
        "failure_files": total_failure_files,
        "errors": len(errors),
        "total_transactions": len(transactions),
        "message": (f"Split response processed: {total_success_files} success, "
                    f"{total_failure_files} failure files "
                    f"({total_matched_refs} batch refs matched out of {len(batch_refs)} expected)")
    }
    if errors:
        result["error_details"] = errors

    logger.info("Split response processing complete: success=%d, failure=%d, errors=%d",
                processed_success, processed_failure, len(errors))

    return jsonify(result)


def _update_credits_from_parsed(txn, parsed_data, xml_type, mapping_config):
    """Update transaction's credit_json with data from parsed XML."""
    credits = txn.get("credit_json")
    if isinstance(credits, str):
        credits = json.loads(credits)
    if not credits:
        return

    # Build lookup from parsed credits by reference
    parsed_credits_map = {}
    for pc in parsed_data.get("credits", []):
        ref = pc.get("reference", "")
        if ref:
            parsed_credits_map[ref] = pc

    logger.debug("Matching %d DB credits against %d parsed %s credits",
                 len(credits), len(parsed_credits_map), xml_type)

    updated = False
    for credit in credits:
        credit_ref = credit.get("credit_reference", "")
        if credit_ref and credit_ref in parsed_credits_map:
            pc = parsed_credits_map[credit_ref]
            prefix = xml_type[:4]  # "init" or "resp"

            if xml_type == "initiation":
                credit["initiation_status"] = pc.get("status", "")
                credit["initiation_remarks"] = pc.get("remarks", "")
                logger.debug("Updated credit %s with initiation data: status=%s",
                             credit_ref, pc.get("status", ""))
            elif xml_type == "response":
                credit["response_status"] = pc.get("status", "")
                credit["response_remarks"] = pc.get("remarks", "")
                credit["response_amount"] = pc.get("amount", "")
                credit["unique_credit_resp_id"] = pc.get("unique_credit_resp_id", "")
                logger.debug("Updated credit %s with response data: status=%s, amount=%s",
                             credit_ref, pc.get("status", ""), pc.get("amount", ""))
            elif xml_type == "response_fail":
                credit["response_fail_status"] = pc.get("status", "")
                credit["response_fail_remarks"] = pc.get("remarks", "")
                credit["unique_credit_resp_id"] = pc.get("unique_credit_resp_id", "")
                logger.debug("Updated credit %s with response_fail data: status=%s",
                             credit_ref, pc.get("status", ""))

            updated = True
            logger.debug("Updated credit %s with %s data", credit_ref, xml_type)
        elif credit_ref:
            logger.warning("Credit ref %s not found in parsed %s credits", credit_ref, xml_type)

    if updated:
        models.update_transaction_credit_json(txn["id"], credits)
