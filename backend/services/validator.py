"""Validation engine for comparing expected vs actual transaction data.

Compares debit and credit entries from initiation/response XMLs against
the original test data. All field names and status codes are driven by
the scheme's mapping configuration — nothing is hardcoded.

Supports separate debit_status_values and credit_status_values per scheme,
and generates human-readable validation descriptions for both initiation
and response phases.
"""
import json
import logging

logger = logging.getLogger(__name__)


def validate_transaction(transaction, initiation_data, response_data, mapping_config,
                         response_fail_data=None):
    """Validate a single transaction against initiation and response data.

    Args:
        transaction: Transaction dict from DB.
        initiation_data: Parsed initiation XML data (or None).
        response_data: Parsed success response XML data (or None).
        mapping_config: Full mapping config dict.
        response_fail_data: Parsed failure response XML data for split response
                           mode (or None).

    Returns:
        Dict with validation results including separate initiation/response
        validation results and descriptions.
    """
    tc_id = transaction.get("tc_id", "unknown")
    logger.info("Validating transaction: tc_id=%s, id=%s (response_fail_data=%s)",
                tc_id, transaction.get("id"),
                "present" if response_fail_data else "absent")

    credits = transaction.get("credit_json")
    if isinstance(credits, str):
        credits = json.loads(credits)
    credits = credits or []

    result = {
        "overall": "PASS",
        "debit_validation": {},
        "credit_validations": [],
        "actual_debit_status": "",
        "actual_debit_remarks": "",
        "initiation_validation": "",
        "initiation_validation_desc": "",
        "response_validation": "",
        "response_validation_desc": "",
        "updated_credits": list(credits)
    }

    # ── Phase 1: Validate initiation ─────────────────────────────────
    if initiation_data:
        init_result = _validate_phase(
            transaction, result["updated_credits"],
            initiation_data, mapping_config, "initiation"
        )
        result["initiation_validation"] = init_result["phase_result"]
        result["initiation_validation_desc"] = init_result["description"]
        result["updated_credits"] = init_result["updated_credits"]

        if init_result["phase_result"] == "FAIL":
            result["overall"] = "FAIL"

        logger.info("Initiation validation: %s — %s",
                     init_result["phase_result"], init_result["description"])

    # ── Phase 2+3: Split response mode (both success and failure data) ──
    if response_data and response_fail_data:
        logger.info("Using split response validation for tc_id=%s", tc_id)
        split_result = _validate_split_response(
            transaction, result["updated_credits"],
            response_data, response_fail_data, mapping_config
        )
        result["response_validation"] = split_result["phase_result"]
        result["response_validation_desc"] = split_result["description"]
        result["actual_debit_status"] = split_result["actual_debit_status"]
        result["actual_debit_remarks"] = split_result["actual_debit_remarks"]
        result["debit_validation"] = split_result["debit_validation"]
        result["credit_validations"] = split_result["credit_validations"]
        result["updated_credits"] = split_result["updated_credits"]

        if split_result["phase_result"] == "FAIL":
            result["overall"] = "FAIL"

        logger.info("Split response validation: %s — %s",
                     split_result["phase_result"], split_result["description"])

    # ── Phase 2: Standard response (non-split schemes) ────────────────
    elif response_data:
        resp_result = _validate_phase(
            transaction, result["updated_credits"],
            response_data, mapping_config, "response"
        )
        result["response_validation"] = resp_result["phase_result"]
        result["response_validation_desc"] = resp_result["description"]
        result["actual_debit_status"] = resp_result["actual_debit_status"]
        result["actual_debit_remarks"] = resp_result["actual_debit_remarks"]
        result["debit_validation"] = resp_result["debit_validation"]
        result["credit_validations"] = resp_result["credit_validations"]
        result["updated_credits"] = resp_result["updated_credits"]

        if resp_result["phase_result"] == "FAIL":
            result["overall"] = "FAIL"

        logger.info("Response validation: %s — %s",
                     resp_result["phase_result"], resp_result["description"])

    # ── Phase 3: Only failure data (edge case — no success file found) ─
    elif response_fail_data:
        logger.info("Only failure response data available for tc_id=%s (no success file)", tc_id)
        fail_result = _validate_phase(
            transaction, result["updated_credits"],
            response_fail_data, mapping_config, "response_fail"
        )
        result["response_validation"] = fail_result["phase_result"]
        result["response_validation_desc"] = "Fail XML only: " + fail_result["description"]
        result["credit_validations"] = fail_result["credit_validations"]
        result["updated_credits"] = fail_result["updated_credits"]

        if fail_result["phase_result"] == "FAIL":
            result["overall"] = "FAIL"

        logger.info("Failure-only response validation: %s — %s",
                     fail_result["phase_result"], fail_result["description"])

    # ── Check expected vs actual overall status ──────────────────────
    expected = (transaction.get("expected_status") or "").upper()
    if expected and result["actual_debit_status"]:
        resp_config = mapping_config.get("response", {})
        debit_sv = _get_debit_status_values(resp_config)

        evaluated = _evaluate_status(result["actual_debit_status"], debit_sv)
        if expected == "SUCCESS" and evaluated != "PASS":
            result["overall"] = "FAIL"
            extra = f"Expected SUCCESS but debit status {result['actual_debit_status']} evaluated as {evaluated}"
            result["response_validation_desc"] += f" {extra}."
        elif expected == "FAILURE" and evaluated != "FAIL":
            result["overall"] = "FAIL"
            extra = f"Expected FAILURE but debit status {result['actual_debit_status']} evaluated as {evaluated}"
            result["response_validation_desc"] += f" {extra}."

    logger.info("Validation complete for tc_id=%s: overall=%s", tc_id, result["overall"])
    return result


def _validate_split_response(transaction, credits, response_data, response_fail_data,
                              mapping_config):
    """Validate credits with strict separation between success and failure response files.

    In split response mode, each credit should appear in exactly one file:
    - Credits in success response → validate fields (account, amount, status)
    - Credits in failure response → mark as FAIL with failure status stored
    - Credits in neither → mark as FAIL (not found)
    - Credits in both (anomaly) → log warning, validate success entry

    Args:
        transaction: Transaction dict from DB.
        credits: Current credit list (may already have initiation data).
        response_data: Parsed success response XML data.
        response_fail_data: Parsed failure response XML data.
        mapping_config: Full mapping config.

    Returns:
        Dict with phase_result, description, debit_validation, credit_validations,
        actual_debit_status, actual_debit_remarks, updated_credits.
    """
    resp_config = mapping_config.get("response", {})
    fail_config = mapping_config.get("response_fail", {})
    debit_sv = _get_debit_status_values(resp_config)
    success_credit_sv = _get_credit_status_values(resp_config)
    fail_credit_sv = _get_credit_status_values(fail_config)

    desc_parts = []
    phase_result = "PASS"
    updated_credits = list(credits)

    # ── Debit validation (from success response) ─────────────────────
    actual_debit = response_data.get("debit", {})
    debit_validation = _validate_debit(transaction, actual_debit, debit_sv)
    actual_debit_status = actual_debit.get("debit_status", "")
    actual_debit_remarks = actual_debit.get("debit_remarks", "")
    logger.debug("[split] Debit validation: status=%s, debit_status=%s",
                 debit_validation["status"], actual_debit_status)

    if debit_validation["status"] == "FAIL":
        phase_result = "FAIL"

    for check in debit_validation.get("checks", []):
        if check["field"] == "debit_status":
            desc_parts.append(f"Debit status {check['value']} ({check['status'].lower()})")
        elif check["field"] == "debit_account" and check["status"] == "FAIL":
            desc_parts.append(
                f"Debit account mismatch: expected {check['expected']}, got {check['actual']}")
        elif check["field"] == "debit_amount" and check["status"] == "FAIL":
            desc_parts.append(
                f"Debit amount mismatch: expected {check['expected']}, got {check['actual']}")

    # ── Build credit reference maps for success and failure ──────────
    success_credits_map = {}
    for c in response_data.get("credits", []):
        ref = c.get("reference", "")
        if ref:
            success_credits_map[ref] = c

    fail_credits_map = {}
    for c in response_fail_data.get("credits", []):
        ref = c.get("reference", "")
        if ref:
            fail_credits_map[ref] = c

    logger.debug("[split] Success credits: %d, Failure credits: %d",
                 len(success_credits_map), len(fail_credits_map))

    # ── Validate each credit with strict separation ──────────────────
    credit_validations = []
    success_count = 0
    fail_count = 0
    not_found_count = 0
    total_credits = len(updated_credits)

    for idx, credit in enumerate(updated_credits):
        credit_ref = credit.get("credit_reference", "")
        credit_val = {"credit_index": idx, "credit_reference": credit_ref, "checks": []}

        in_success = credit_ref in success_credits_map
        in_failure = credit_ref in fail_credits_map

        logger.debug("[split] Credit %d (ref=%s): in_success=%s, in_failure=%s",
                     idx, credit_ref, in_success, in_failure)

        if in_success and in_failure:
            # Anomaly: credit found in both files
            logger.warning("[split] Credit %d (ref=%s) found in BOTH success and failure "
                           "response files — validating success entry only", idx, credit_ref)

        if in_success:
            # ── Credit in success response: full field validation ─────
            success_credit = success_credits_map[credit_ref]

            # Store response status/remarks
            credit["response_status"] = success_credit.get("status", "")
            credit["response_remarks"] = success_credit.get("remarks", "")
            credit["response_amount"] = success_credit.get("amount", "")
            logger.debug("[split] Credit %d (ref=%s) SUCCESS: status=%s, amount=%s",
                         idx, credit_ref, credit["response_status"], credit["response_amount"])

            # Validate credit amount
            expected_amount = str(credit.get("amount", "")).strip()
            actual_amount = str(success_credit.get("amount", "")).strip()
            if expected_amount and actual_amount:
                try:
                    if abs(float(expected_amount) - float(actual_amount)) > 0.01:
                        credit_val["checks"].append({
                            "field": "amount",
                            "status": "FAIL",
                            "expected": expected_amount,
                            "actual": actual_amount,
                            "message": f"Amount mismatch: expected {expected_amount}, got {actual_amount}"
                        })
                        logger.debug("[split] Credit %d (ref=%s) amount MISMATCH: "
                                     "expected=%s, actual=%s",
                                     idx, credit_ref, expected_amount, actual_amount)
                except ValueError:
                    pass

            # Validate credit account
            expected_acct = str(credit.get("account", "")).strip()
            actual_acct = str(success_credit.get("account", "")).strip()
            if expected_acct and actual_acct and expected_acct != actual_acct:
                credit_val["checks"].append({
                    "field": "account",
                    "status": "FAIL",
                    "expected": expected_acct,
                    "actual": actual_acct,
                    "message": f"Account mismatch: expected {expected_acct}, got {actual_acct}"
                })
                logger.debug("[split] Credit %d (ref=%s) account MISMATCH: "
                             "expected=%s, actual=%s",
                             idx, credit_ref, expected_acct, actual_acct)

            # Validate credit status using success credit_status_values
            credit_status = success_credit.get("status", "")
            if success_credit_sv and credit_status:
                status_result = _evaluate_status(credit_status, success_credit_sv)
                credit_val["checks"].append({
                    "field": "credit_status",
                    "status": status_result,
                    "value": credit_status,
                    "message": f"Credit status {credit_status} ({status_result.lower()})"
                })
                logger.debug("[split] Credit %d (ref=%s) status evaluation: "
                             "code=%s, result=%s",
                             idx, credit_ref, credit_status, status_result)
                if status_result == "FAIL":
                    phase_result = "FAIL"

            # Determine credit-level result
            failed_checks = [c for c in credit_val["checks"] if c["status"] == "FAIL"]
            credit_val["status"] = "FAIL" if failed_checks else "PASS"
            credit["validation_result"] = credit_val["status"]

            if credit_val["status"] == "PASS":
                success_count += 1
            else:
                phase_result = "FAIL"

            logger.debug("[split] Credit %d (ref=%s) final result: %s",
                         idx, credit_ref, credit_val["status"])

        elif in_failure:
            # ── Credit in failure response: mark as FAIL ─────────────
            fail_credit = fail_credits_map[credit_ref]

            # Store failure response status/remarks
            credit["response_fail_status"] = fail_credit.get("status", "")
            credit["response_fail_remarks"] = fail_credit.get("remarks", "")
            logger.debug("[split] Credit %d (ref=%s) FAILURE: status=%s, remarks=%s",
                         idx, credit_ref,
                         credit["response_fail_status"],
                         credit["response_fail_remarks"])

            # Validate failure status code if configured
            fail_status = fail_credit.get("status", "")
            if fail_credit_sv and fail_status:
                status_result = _evaluate_status(fail_status, fail_credit_sv)
                credit_val["checks"].append({
                    "field": "credit_status",
                    "status": "FAIL",
                    "value": fail_status,
                    "message": f"Credit rejected in failure response: status {fail_status} ({status_result.lower()})"
                })
                logger.debug("[split] Credit %d (ref=%s) fail status evaluation: "
                             "code=%s, result=%s",
                             idx, credit_ref, fail_status, status_result)
            else:
                credit_val["checks"].append({
                    "field": "credit_status",
                    "status": "FAIL",
                    "value": fail_status or "",
                    "message": "Credit found in failure response XML"
                })

            credit_val["status"] = "FAIL"
            credit["validation_result"] = "FAIL"
            phase_result = "FAIL"
            fail_count += 1
            logger.debug("[split] Credit %d (ref=%s) marked FAIL (in failure response)",
                         idx, credit_ref)

        else:
            # ── Credit in neither file ───────────────────────────────
            credit_val["checks"].append({
                "field": "response_match",
                "status": "FAIL",
                "message": f"Credit ref '{credit_ref}' not found in any response XML (success or failure)"
            })
            credit_val["status"] = "FAIL"
            credit["validation_result"] = "FAIL"
            phase_result = "FAIL"
            not_found_count += 1
            logger.warning("[split] Credit %d (ref=%s) NOT FOUND in either success or failure response",
                           idx, credit_ref)

        credit_validations.append(credit_val)

    # ── Build description ────────────────────────────────────────────
    logger.info("[split] Credit validation summary: %d success, %d failed, %d not found "
                "(out of %d total)",
                success_count, fail_count, not_found_count, total_credits)

    if total_credits > 0:
        parts = []
        if success_count > 0:
            parts.append(f"{success_count} success")
        if fail_count > 0:
            parts.append(f"{fail_count} failed")
        if not_found_count > 0:
            parts.append(f"{not_found_count} not found")
        desc_parts.append(f"Credits: {', '.join(parts)} (of {total_credits} total)")

        # Add details for failed validations
        for cv in credit_validations:
            if cv["status"] == "FAIL":
                ref = cv.get("credit_reference", f"#{cv['credit_index']}")
                for check in cv.get("checks", []):
                    if check["status"] == "FAIL":
                        desc_parts.append(f"Credit {ref}: {check['message']}")

    description = ". ".join(desc_parts) + "." if desc_parts else ""

    return {
        "phase_result": phase_result,
        "description": description,
        "debit_validation": debit_validation,
        "credit_validations": credit_validations,
        "actual_debit_status": actual_debit_status,
        "actual_debit_remarks": actual_debit_remarks,
        "updated_credits": updated_credits,
    }


def _validate_phase(transaction, credits, phase_data, mapping_config, phase):
    """Validate a single phase (initiation or response).

    Args:
        transaction: Transaction dict from DB.
        credits: Current credit list (may already have initiation data).
        phase_data: Parsed XML data for this phase.
        mapping_config: Full mapping config.
        phase: 'initiation' or 'response'.

    Returns:
        Dict with phase_result, description, debit_validation, credit_validations,
        actual_debit_status, actual_debit_remarks, updated_credits.
    """
    phase_config = mapping_config.get(phase, {})
    debit_sv = _get_debit_status_values(phase_config)
    credit_sv = _get_credit_status_values(phase_config)

    desc_parts = []
    phase_result = "PASS"
    updated_credits = list(credits)

    # ── Debit validation ─────────────────────────────────────────────
    actual_debit = phase_data.get("debit", {})
    debit_validation = _validate_debit(transaction, actual_debit, debit_sv)
    actual_debit_status = actual_debit.get("debit_status", "")
    actual_debit_remarks = actual_debit.get("debit_remarks", "")

    if debit_validation["status"] == "FAIL":
        phase_result = "FAIL"

    # Build debit description
    for check in debit_validation.get("checks", []):
        if check["field"] == "debit_status":
            desc_parts.append(f"Debit status {check['value']} ({check['status'].lower()})")
        elif check["field"] == "debit_account" and check["status"] == "FAIL":
            desc_parts.append(
                f"Debit account mismatch: expected {check['expected']}, got {check['actual']}")
        elif check["field"] == "debit_amount" and check["status"] == "FAIL":
            desc_parts.append(
                f"Debit amount mismatch: expected {check['expected']}, got {check['actual']}")

    # ── Credit validation ────────────────────────────────────────────
    # Build maps of credits from this phase's XML
    phase_credits_map = {}
    for c in phase_data.get("credits", []):
        ref = c.get("reference", "")
        if ref:
            phase_credits_map[ref] = c

    logger.debug("[%s] Phase credits found: %d", phase, len(phase_credits_map))

    credit_validations = []
    passed_credits = 0
    total_credits = len(updated_credits)

    for idx, credit in enumerate(updated_credits):
        credit_ref = credit.get("credit_reference", "")
        credit_val = {"credit_index": idx, "credit_reference": credit_ref, "checks": []}

        phase_credit = phase_credits_map.get(credit_ref)
        if phase_credit:
            # Store phase-specific status/remarks
            status_key = f"{phase}_status"
            remarks_key = f"{phase}_remarks"
            credit[status_key] = phase_credit.get("status", "")
            credit[remarks_key] = phase_credit.get("remarks", "")

            if phase == "response":
                credit["response_amount"] = phase_credit.get("amount", "")

                # Validate credit amount (response only)
                expected_amount = str(credit.get("amount", "")).strip()
                actual_amount = str(phase_credit.get("amount", "")).strip()
                if expected_amount and actual_amount:
                    try:
                        if abs(float(expected_amount) - float(actual_amount)) > 0.01:
                            credit_val["checks"].append({
                                "field": "amount",
                                "status": "FAIL",
                                "expected": expected_amount,
                                "actual": actual_amount,
                                "message": f"Amount mismatch: expected {expected_amount}, got {actual_amount}"
                            })
                    except ValueError:
                        pass

                # Validate credit account (response only)
                expected_acct = str(credit.get("account", "")).strip()
                actual_acct = str(phase_credit.get("account", "")).strip()
                if expected_acct and actual_acct and expected_acct != actual_acct:
                    credit_val["checks"].append({
                        "field": "account",
                        "status": "FAIL",
                        "expected": expected_acct,
                        "actual": actual_acct,
                        "message": f"Account mismatch: expected {expected_acct}, got {actual_acct}"
                    })

            # Validate credit status using credit_status_values
            credit_status = phase_credit.get("status", "")
            if credit_sv and credit_status:
                status_result = _evaluate_status(credit_status, credit_sv)
                credit_val["checks"].append({
                    "field": "credit_status",
                    "status": status_result,
                    "value": credit_status,
                    "message": f"Credit status {credit_status} ({status_result.lower()})"
                })
                if status_result == "FAIL":
                    phase_result = "FAIL"

            logger.debug("[%s] Credit %d (ref=%s) matched: status=%s",
                         phase, idx, credit_ref, credit_status)
        else:
            credit_val["checks"].append({
                "field": f"{phase}_match",
                "status": "FAIL",
                "message": f"Credit ref '{credit_ref}' not found in {phase} XML"
            })
            logger.warning("[%s] Credit %d (ref=%s) NOT found", phase, idx, credit_ref)

        # Determine credit-level result
        failed_checks = [c for c in credit_val["checks"] if c["status"] == "FAIL"]
        credit_val["status"] = "FAIL" if failed_checks else "PASS"
        credit["validation_result"] = credit_val["status"]

        if credit_val["status"] == "FAIL":
            phase_result = "FAIL"
        else:
            passed_credits += 1

        credit_validations.append(credit_val)

    # Build credit description
    if total_credits > 0:
        if passed_credits == total_credits:
            desc_parts.append(f"All {total_credits} credits passed")
        else:
            desc_parts.append(f"{passed_credits}/{total_credits} credits passed")
            for cv in credit_validations:
                if cv["status"] == "FAIL":
                    ref = cv.get("credit_reference", f"#{cv['credit_index']}")
                    for check in cv.get("checks", []):
                        if check["status"] == "FAIL":
                            desc_parts.append(f"Credit {ref}: {check['message']}")

    description = ". ".join(desc_parts) + "." if desc_parts else ""

    return {
        "phase_result": phase_result,
        "description": description,
        "debit_validation": debit_validation,
        "credit_validations": credit_validations,
        "actual_debit_status": actual_debit_status,
        "actual_debit_remarks": actual_debit_remarks,
        "updated_credits": updated_credits,
    }


def _validate_debit(transaction, actual_debit, debit_status_values):
    """Validate debit-level fields."""
    checks = []

    # Validate debit account
    expected_acct = str(transaction.get("debit_account", "")).strip()
    actual_acct = str(actual_debit.get("debit_account", "")).strip()
    if expected_acct and actual_acct:
        if expected_acct != actual_acct:
            checks.append({
                "field": "debit_account",
                "status": "FAIL",
                "expected": expected_acct,
                "actual": actual_acct
            })
        else:
            checks.append({
                "field": "debit_account",
                "status": "PASS",
                "value": actual_acct
            })

    # Validate debit amount
    expected_amt = transaction.get("debit_amount")
    actual_amt_str = actual_debit.get("debit_amount", "")
    if expected_amt and actual_amt_str:
        try:
            if abs(float(expected_amt) - float(actual_amt_str)) > 0.01:
                checks.append({
                    "field": "debit_amount",
                    "status": "FAIL",
                    "expected": str(expected_amt),
                    "actual": actual_amt_str
                })
            else:
                checks.append({
                    "field": "debit_amount",
                    "status": "PASS",
                    "value": actual_amt_str
                })
        except ValueError:
            pass

    # Validate debit status using debit_status_values
    debit_status = actual_debit.get("debit_status", "")
    if debit_status_values and debit_status:
        status_result = _evaluate_status(debit_status, debit_status_values)
        checks.append({
            "field": "debit_status",
            "status": status_result,
            "value": debit_status,
            "message": f"Debit status '{debit_status}' evaluated as {status_result}"
        })

    failed = [c for c in checks if c["status"] == "FAIL"]
    return {
        "checks": checks,
        "status": "FAIL" if failed else "PASS"
    }


def _get_debit_status_values(type_config):
    """Get debit status values with backward compatibility."""
    return type_config.get("debit_status_values", type_config.get("status_values", {}))


def _get_credit_status_values(type_config):
    """Get credit status values with backward compatibility."""
    return type_config.get("credit_status_values", type_config.get("status_values", {}))


def _evaluate_status(status_code, status_values):
    """Evaluate a status code against user-defined status values.

    Args:
        status_code: The status code from XML (e.g., "R00", "P01").
        status_values: Dict like {"success": ["R00"], "failure": ["R01"], "pending": ["P01"]}

    Returns:
        "PASS" if status is in success list,
        "FAIL" if in failure list,
        "PENDING" if in pending list,
        "UNKNOWN" otherwise.
    """
    for category, codes in status_values.items():
        if status_code in codes:
            if category == "success":
                return "PASS"
            elif category == "failure":
                return "FAIL"
            elif category == "pending":
                return "PENDING"
    logger.warning("Status code '%s' not found in any configured status category", status_code)
    return "UNKNOWN"
