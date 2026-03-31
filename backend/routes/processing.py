"""Processing routes — GBB submission, initiation/response JSON handling."""
import json
import logging
import os
import requests as http_requests
from flask import Blueprint, request, jsonify
from backend import models
from backend.config import (
    REQUEST_JSON_DIR, INITIATION_JSON_DIR, RESPONSE_JSON_DIR, GBB_ENDPOINT_URL
)
from backend.services.file_matcher import (
    find_matching_files, find_matching_files_multi, find_matching_files_split
)
from backend.services.json_parser import (
    parse_json_file, parse_json_data, parse_multi_batch_json
)

logger = logging.getLogger(__name__)
processing_bp = Blueprint("processing", __name__)


@processing_bp.route("/test-runs/<int:run_id>/submit-gbb", methods=["POST"])
def submit_to_gbb(run_id):
    """Submit generated request JSONs to GBB via HTTP POST.

    Reads each generated JSON file and POSTs it to the GBB endpoint.
    Accepts optional 'gbb_url' in request body to override the default endpoint.
    """
    logger.info("POST /api/processing/test-runs/%d/submit-gbb", run_id)

    run = models.get_test_run(run_id)
    if not run:
        return jsonify({"error": "Test run not found"}), 404

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found"}), 404

    # Allow overriding GBB URL from request body
    req_data = request.get_json(silent=True) or {}
    gbb_url = req_data.get("gbb_url", GBB_ENDPOINT_URL)

    submitted = 0
    errors = []

    for txn in transactions:
        json_path = txn.get("request_xml_path")
        if not json_path or not os.path.isfile(json_path):
            logger.warning("No request JSON found for transaction %d (path=%s)",
                           txn["id"], json_path)
            errors.append({
                "transaction_id": txn["id"],
                "error": "No request JSON file found"
            })
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # POST to GBB endpoint
            response = http_requests.post(
                gbb_url,
                json={
                    "test_run_id": run_id,
                    "transaction_id": txn["id"],
                    "payload": payload
                },
                timeout=30
            )

            if response.status_code == 200:
                models.update_transaction(txn["id"], status="submitted")
                submitted += 1
                logger.debug("Submitted JSON for tc_id=%s to GBB: %s (status=%d)",
                             txn["tc_id"], gbb_url, response.status_code)
            else:
                logger.warning("GBB returned status %d for tc_id=%s",
                               response.status_code, txn["tc_id"])
                models.update_transaction(txn["id"], status="submitted")
                submitted += 1

        except http_requests.exceptions.ConnectionError:
            logger.warning("Could not connect to GBB at %s for tc_id=%s — marking as submitted",
                           gbb_url, txn["tc_id"])
            # Still mark as submitted so the flow can continue
            models.update_transaction(txn["id"], status="submitted")
            submitted += 1
        except Exception as e:
            logger.exception("Failed to submit JSON for transaction %d", txn["id"])
            errors.append({"transaction_id": txn["id"], "error": str(e)})

    if submitted > 0:
        models.update_test_run_status(run_id, "initiated")

    logger.info("Submitted %d/%d JSONs to GBB (url=%s)", submitted, len(transactions), gbb_url)

    return jsonify({
        "submitted": submitted,
        "total": len(transactions),
        "gbb_url": gbb_url,
        "errors": len(errors),
        "message": f"Submitted {submitted} JSON requests to GBB",
        "error_details": errors if errors else None
    })


# ── GBB Controller Endpoints ─────────────────────────────────────────

@processing_bp.route("/gbb/initiation", methods=["POST"])
def receive_initiation():
    """Receive initiation JSON from GBB system.

    GBB posts the initiation JSON to this endpoint after processing.
    The JSON is saved to disk and parsed to update transaction data.

    Expected request body:
    {
        "test_run_id": 1,          // optional — helps narrow the search
        "payload": { ... }          // the initiation JSON
    }
    """
    logger.info("POST /api/processing/gbb/initiation — receiving initiation from GBB")
    return _receive_gbb_json("initiation", INITIATION_JSON_DIR)


@processing_bp.route("/gbb/response", methods=["POST"])
def receive_response():
    """Receive response JSON from GBB system.

    GBB posts the response JSON to this endpoint after processing.

    Expected request body:
    {
        "test_run_id": 1,          // optional
        "payload": { ... }          // the response JSON
    }
    """
    logger.info("POST /api/processing/gbb/response — receiving response from GBB")
    return _receive_gbb_json("response", RESPONSE_JSON_DIR)


def _receive_gbb_json(json_type, save_dir):
    """Generic handler for receiving JSON from GBB (initiation or response)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400

    payload = data.get("payload")
    if not payload:
        return jsonify({"error": "No 'payload' field in request body"}), 400

    test_run_id = data.get("test_run_id")

    # Determine scheme and mapping config
    # Strategy: if test_run_id provided, use that scheme.
    # Otherwise, try to find the transaction by batch reference.
    scheme = None
    mapping_config = None

    if test_run_id:
        run = models.get_test_run(test_run_id)
        if run:
            scheme = models.get_scheme(run["scheme_id"])

    if scheme:
        mapping_config = scheme.get("mapping_config")
        if isinstance(mapping_config, str):
            mapping_config = json.loads(mapping_config)

    if not mapping_config or not mapping_config.get(json_type):
        # Try to find by iterating through recent test runs
        logger.warning("No mapping config found directly, searching test runs...")
        return jsonify({"error": f"Cannot determine scheme mapping. Provide test_run_id."}), 400

    # Parse the JSON to extract batch reference
    try:
        config = mapping_config.get(json_type, {})
        root_key = config.get("root_key", "")
        batch_container = config.get("batch_container", "")
        batch_ref_field = config.get("batch_reference_field", "")

        root_obj = payload
        if root_key and root_key in payload:
            root_obj = payload[root_key]

        # Handle multiple batches in a single payload
        batches = []
        if batch_container and batch_container in root_obj:
            container = root_obj[batch_container]
            if isinstance(container, list):
                batches = container
            else:
                batches = [container]
        else:
            batches = [root_obj]

        processed = 0
        errors = []

        for batch_idx, batch_obj in enumerate(batches):
            from backend.services.json_parser import _get_json_value
            batch_ref = _get_json_value(batch_obj, batch_ref_field) if batch_ref_field else None

            if not batch_ref:
                errors.append({"batch_index": batch_idx, "error": "Could not extract batch reference"})
                continue

            # Find the transaction by batch reference
            txn = models.get_transaction_by_batch_ref(batch_ref)
            if not txn:
                errors.append({"batch_reference": batch_ref, "error": "No matching transaction found"})
                continue

            # Save JSON to disk
            os.makedirs(save_dir, exist_ok=True)
            filename = f"{batch_ref}_{json_type}.json"
            filepath = os.path.join(save_dir, filename)

            # Save just this batch's data wrapped in the expected structure
            save_data = payload  # Save full payload
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            logger.info("Saved %s JSON to: %s", json_type, filepath)

            # Parse using the full mapping config
            parsed = parse_json_data(payload, json_type, mapping_config)

            # Determine if this is split response
            is_split = False
            if json_type == "response" and test_run_id:
                run = models.get_test_run(test_run_id) if test_run_id else None
                if run:
                    s = models.get_scheme(run["scheme_id"])
                    is_split = s and s.get("is_response_xml_split") == "Y"

            if json_type == "response" and is_split:
                from backend.services.json_parser import check_success_indicator
                file_class = check_success_indicator(payload, config)
                _apply_parsed_data(txn, parsed, file_class, mapping_config, filepath)
            else:
                _apply_parsed_data(txn, parsed, json_type, mapping_config, filepath)

            processed += 1
            logger.info("Processed %s JSON for batch_ref=%s (tc_id=%s)",
                        json_type, batch_ref, txn["tc_id"])

        return jsonify({
            "processed": processed,
            "errors": len(errors),
            "message": f"Processed {processed} {json_type} batch(es)",
            "error_details": errors if errors else None
        })

    except Exception as e:
        logger.exception("Failed to process received %s JSON", json_type)
        return jsonify({"error": f"Failed to process {json_type} JSON: {str(e)}"}), 500


def _apply_parsed_data(txn, parsed, json_type, mapping_config, filepath):
    """Apply parsed JSON data to a transaction (same logic as _process_json_files)."""
    debit_data = parsed.get("debit", {})
    credit_data = parsed.get("credits", [])

    path_field = "initiation_xml_path" if json_type == "initiation" else "response_xml_path"
    status_val = f"{json_type}_matched"

    update_data = {
        path_field: filepath,
        "status": status_val,
    }

    if json_type in ("initiation", "response"):
        update_data[f"debit_fields_{json_type}"] = json.dumps(debit_data)
        update_data[f"credit_fields_{json_type}"] = json.dumps(credit_data)

        if debit_data.get("debit_status"):
            update_data["actual_debit_status"] = debit_data["debit_status"]
        if debit_data.get("debit_remarks"):
            update_data["actual_debit_remarks"] = debit_data["debit_remarks"]

        if json_type == "initiation":
            update_data["debit_initiation_status"] = debit_data.get("debit_status", "")
            update_data["debit_initiation_remarks"] = debit_data.get("debit_remarks", "")

    elif json_type == "response_fail":
        update_data["credit_fields_response_failed"] = json.dumps(credit_data)

    models.update_transaction(txn["id"], **update_data)

    # Update credit_json with parsed data
    _update_credits_from_parsed(txn, parsed, json_type, mapping_config)


# ── Folder-based Processing (fallback) ───────────────────────────────

@processing_bp.route("/test-runs/<int:run_id>/process-initiation", methods=["POST"])
def process_initiation(run_id):
    """Scan initiation folder, match files to transactions by batch reference."""
    logger.info("POST /api/processing/test-runs/%d/process-initiation", run_id)
    return _process_json_files(run_id, "initiation", INITIATION_JSON_DIR)


@processing_bp.route("/test-runs/<int:run_id>/process-response", methods=["POST"])
def process_response(run_id):
    """Scan response folder, match files to transactions by batch reference."""
    logger.info("POST /api/processing/test-runs/%d/process-response", run_id)

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
        return _process_split_response_files(run_id, RESPONSE_JSON_DIR)
    else:
        logger.info("Using standard response processing for run %d", run_id)
        return _process_json_files(run_id, "response", RESPONSE_JSON_DIR)


def _process_json_files(run_id, json_type, folder_path):
    """Generic processor for initiation/response JSON files.

    Supports both single-batch and multi-batch JSON files.
    In multi-batch mode, a single file can contain multiple BatchDetails entries,
    each matching a different transaction by batch_reference.
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

    if not mapping_config or not mapping_config.get(json_type):
        return jsonify({
            "error": f"Scheme has no {json_type} mapping configured"
        }), 400

    # Check if the scheme uses multi-batch
    request_config = mapping_config.get("request", {})
    is_multi_batch = request_config.get("is_multi_batch", False)

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
        return jsonify({"error": "No transactions have batch references. Generate JSONs first."}), 400

    logger.info("Looking for %s files matching %d batch references in %s (multi_batch=%s)",
                json_type, len(batch_refs), folder_path, is_multi_batch)

    processed = 0
    errors = []

    if is_multi_batch:
        # Multi-batch mode: files may contain multiple batches
        matched_files = find_matching_files_multi(
            folder_path, json_type, mapping_config, set(batch_refs.keys())
        )

        for batch_ref, file_info_list in matched_files.items():
            txn = batch_refs[batch_ref]
            try:
                all_credit_data = []
                all_file_paths = []
                debit_data = {}

                for filepath, batch_idx in file_info_list:
                    # Parse only the specific batch from the multi-batch file
                    all_batches = parse_multi_batch_json(filepath, json_type, mapping_config)
                    if batch_idx < len(all_batches):
                        parsed = all_batches[batch_idx]
                    else:
                        logger.warning("Batch index %d out of range in %s", batch_idx, filepath)
                        continue

                    file_debit = parsed.get("debit", {})
                    file_credits = parsed.get("credits", [])

                    if not debit_data:
                        debit_data = file_debit
                    all_credit_data.extend(file_credits)
                    all_file_paths.append(filepath)

                    _update_credits_from_parsed(txn, parsed, json_type, mapping_config)

                path_field = f"{json_type}_xml_path"
                status_val = f"{json_type}_matched"

                update_data = {
                    path_field: ";".join(set(all_file_paths)),
                    "status": status_val,
                    f"debit_fields_{json_type}": json.dumps(debit_data),
                    f"credit_fields_{json_type}": json.dumps(all_credit_data),
                }

                if debit_data.get("debit_status"):
                    update_data["actual_debit_status"] = debit_data["debit_status"]
                if debit_data.get("debit_remarks"):
                    update_data["actual_debit_remarks"] = debit_data["debit_remarks"]

                if json_type == "initiation":
                    update_data["debit_initiation_status"] = debit_data.get("debit_status", "")
                    update_data["debit_initiation_remarks"] = debit_data.get("debit_remarks", "")

                models.update_transaction(txn["id"], **update_data)
                processed += 1

            except Exception as e:
                logger.exception("Failed to process multi-batch %s for batch_ref=%s", json_type, batch_ref)
                errors.append({"batch_reference": batch_ref, "error": str(e)})
    else:
        # Single-batch mode: one file per transaction
        matched_files = find_matching_files(
            folder_path, json_type, mapping_config, set(batch_refs.keys())
        )

        for batch_ref, filepaths in matched_files.items():
            txn = batch_refs[batch_ref]
            try:
                if json_type != "response":
                    filepaths = filepaths[:1]

                all_credit_data = []
                all_file_paths = []
                debit_data = {}

                for filepath in filepaths:
                    parsed = parse_json_file(filepath, json_type, mapping_config)
                    file_debit = parsed.get("debit", {})
                    file_credits = parsed.get("credits", [])

                    if not debit_data:
                        debit_data = file_debit

                    all_credit_data.extend(file_credits)
                    all_file_paths.append(filepath)

                    _update_credits_from_parsed(txn, parsed, json_type, mapping_config)

                path_field = f"{json_type}_xml_path"
                status_val = f"{json_type}_matched"

                update_data = {
                    path_field: ";".join(all_file_paths),
                    "status": status_val,
                    f"debit_fields_{json_type}": json.dumps(debit_data),
                    f"credit_fields_{json_type}": json.dumps(all_credit_data),
                }

                if debit_data.get("debit_status"):
                    update_data["actual_debit_status"] = debit_data["debit_status"]
                if debit_data.get("debit_remarks"):
                    update_data["actual_debit_remarks"] = debit_data["debit_remarks"]

                if json_type == "initiation":
                    update_data["debit_initiation_status"] = debit_data.get("debit_status", "")
                    update_data["debit_initiation_remarks"] = debit_data.get("debit_remarks", "")

                models.update_transaction(txn["id"], **update_data)

                processed += 1
                logger.info("Processed %d %s file(s) for tc_id=%s (batch_ref=%s)",
                            len(filepaths), json_type, txn["tc_id"], batch_ref)

            except Exception as e:
                logger.exception("Failed to process %s file for batch_ref=%s", json_type, batch_ref)
                errors.append({"batch_reference": batch_ref, "error": str(e)})

    new_status = f"{json_type}_processed"
    models.update_test_run_status(run_id, new_status)

    result = {
        "matched": processed,
        "processed": processed,
        "errors": len(errors),
        "total_transactions": len(transactions),
        "multi_batch": is_multi_batch,
        "message": f"Processed {processed} {json_type} batches"
    }
    if errors:
        result["error_details"] = errors

    return jsonify(result)


def _process_split_response_files(run_id, folder_path):
    """Process split response JSON files — separate success and failure JSONs."""
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
        return jsonify({"error": "Scheme has no response mapping configured"}), 400

    if not mapping_config.get("response_fail"):
        return jsonify({"error": "Scheme has no response_fail mapping configured"}), 400

    response_config = mapping_config.get("response", {})
    indicator = response_config.get("success_indicator_path", "") or response_config.get("success_indicator_tag", "")
    if not indicator:
        return jsonify({"error": "No success indicator configured."}), 400

    transactions = models.get_transactions_by_run(run_id)
    if not transactions:
        return jsonify({"error": "No transactions found"}), 404

    batch_refs = {}
    for txn in transactions:
        br = txn.get("batch_reference")
        if br:
            batch_refs[br] = txn

    if not batch_refs:
        return jsonify({"error": "No transactions have batch references."}), 400

    success_matches, failure_matches = find_matching_files_split(
        folder_path, mapping_config, set(batch_refs.keys())
    )

    processed_success = 0
    processed_failure = 0
    errors = []

    # Process success response files
    for batch_ref, filepaths in success_matches.items():
        txn = batch_refs[batch_ref]
        try:
            all_credit_data = []
            all_file_paths = []
            debit_data = {}

            for filepath in filepaths:
                parsed = parse_json_file(filepath, "response", mapping_config)
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
            if debit_data.get("debit_remarks"):
                update_data["actual_debit_remarks"] = debit_data["debit_remarks"]

            models.update_transaction(txn["id"], **update_data)
            processed_success += 1

        except Exception as e:
            logger.exception("Failed to process success response for batch_ref=%s", batch_ref)
            errors.append({"batch_reference": batch_ref, "type": "success", "error": str(e)})

    # Process failure response files
    for batch_ref, filepaths in failure_matches.items():
        txn = batch_refs[batch_ref]
        try:
            all_credit_data = []
            all_file_paths = []

            for filepath in filepaths:
                parsed = parse_json_file(filepath, "response_fail", mapping_config)
                all_credit_data.extend(parsed.get("credits", []))
                all_file_paths.append(filepath)
                _update_credits_from_parsed(txn, parsed, "response_fail", mapping_config)

            update_data = {
                "credit_fields_response_failed": json.dumps(all_credit_data),
            }

            if batch_ref not in success_matches:
                update_data["response_xml_path"] = ";".join(all_file_paths)
                update_data["status"] = "response_matched"

            models.update_transaction(txn["id"], **update_data)
            processed_failure += 1

        except Exception as e:
            logger.exception("Failed to process failure response for batch_ref=%s", batch_ref)
            errors.append({"batch_reference": batch_ref, "type": "failure", "error": str(e)})

    models.update_test_run_status(run_id, "response_processed")

    total_processed = processed_success + processed_failure
    total_success_files = sum(len(fps) for fps in success_matches.values())
    total_failure_files = sum(len(fps) for fps in failure_matches.values())

    result = {
        "matched": len(success_matches) + len(failure_matches),
        "matched_files": total_success_files + total_failure_files,
        "processed": total_processed,
        "success_files": total_success_files,
        "failure_files": total_failure_files,
        "errors": len(errors),
        "total_transactions": len(transactions),
        "message": (f"Split response processed: {total_success_files} success, "
                    f"{total_failure_files} failure files")
    }
    if errors:
        result["error_details"] = errors

    return jsonify(result)


def _update_credits_from_parsed(txn, parsed_data, json_type, mapping_config):
    """Update transaction's credit_json with data from parsed JSON."""
    credits = txn.get("credit_json")
    if isinstance(credits, str):
        credits = json.loads(credits)
    if not credits:
        return

    parsed_credits_map = {}
    for pc in parsed_data.get("credits", []):
        ref = pc.get("reference", "")
        if ref:
            parsed_credits_map[ref] = pc

    updated = False
    for credit in credits:
        credit_ref = credit.get("credit_reference", "")
        if credit_ref and credit_ref in parsed_credits_map:
            pc = parsed_credits_map[credit_ref]

            if json_type == "initiation":
                credit["initiation_status"] = pc.get("status", "")
                credit["initiation_remarks"] = pc.get("remarks", "")
            elif json_type == "response":
                credit["response_status"] = pc.get("status", "")
                credit["response_remarks"] = pc.get("remarks", "")
                credit["response_amount"] = pc.get("amount", "")
                credit["unique_credit_resp_id"] = pc.get("unique_credit_resp_id", "")
            elif json_type == "response_fail":
                credit["response_fail_status"] = pc.get("status", "")
                credit["response_fail_remarks"] = pc.get("remarks", "")
                credit["unique_credit_resp_id"] = pc.get("unique_credit_resp_id", "")

            updated = True

    if updated:
        models.update_transaction_credit_json(txn["id"], credits)
