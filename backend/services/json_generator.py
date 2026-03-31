"""Generate request JSON files from mapping config and transaction data.

Builds JSON objects programmatically based on the scheme's mapping configuration.
Supports nested objects, arrays, and various field source types.
"""
import json
import logging
import os
import re
from datetime import datetime

from backend.services.auto_generator import generate_value

logger = logging.getLogger(__name__)


def generate_request_json(transaction, mapping_config, output_dir, filename_pattern=None):
    """Generate a single request JSON file for a transaction (single-batch mode).

    Args:
        transaction: dict with keys from DB (debit_account, credit_json, etc.)
        mapping_config: The full mapping config dict from the scheme.
        output_dir: Directory to write the JSON file.
        filename_pattern: Optional dict with 'prefix' and 'date_format' for filename generation.

    Returns:
        Tuple of (json_file_path, batch_reference, updated_credit_json).
    """
    request_config = mapping_config.get("request", {})
    if not request_config:
        raise ValueError("No 'request' section found in mapping config")

    root_key = request_config.get("root_key", "Payments")
    batch_container = request_config.get("batch_container", "BatchDetails")

    logger.info("Generating request JSON for transaction id=%s, tc_id=%s",
                transaction.get("id"), transaction.get("tc_id"))

    # Generate filename from pattern
    generated_filename = _generate_filename_from_pattern(filename_pattern)

    # Build the single batch object
    batch_obj, batch_reference, credits = _build_batch_object(
        transaction, request_config, generated_filename=generated_filename
    )

    # Build the top-level JSON structure
    root_obj = _build_top_level(request_config, transaction, batch_reference, generated_filename)

    # Wrap batch object in the batch container array
    root_obj[batch_container] = [batch_obj]

    # Build final JSON: {root_key: root_obj}
    result = {root_key: root_obj}

    # Write to file
    if not generated_filename:
        tc_id = transaction.get("tc_id", "unknown")
        batch_ref_safe = batch_reference or "nobatch"
        generated_filename = f"{tc_id}_{batch_ref_safe}.json"
    filepath = os.path.join(output_dir, generated_filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info("Request JSON generated: %s (batch_ref=%s)", filepath, batch_reference)
    return filepath, batch_reference, credits


def generate_multi_batch_json(transactions, mapping_config, output_dir, filename_pattern=None):
    """Generate a single JSON file with multiple BatchDetails entries (multi-batch mode).

    Each transaction in the list becomes one batch entry within the same request JSON.
    Used when multiple Excel rows share the same tc_id.

    Args:
        transactions: List of transaction dicts (all sharing the same tc_id).
        mapping_config: The full mapping config dict from the scheme.
        output_dir: Directory to write the JSON file.
        filename_pattern: Optional dict with 'prefix' and 'date_format' for filename generation.

    Returns:
        Tuple of (json_file_path, list_of_batch_references, list_of_updated_credits).
    """
    request_config = mapping_config.get("request", {})
    if not request_config:
        raise ValueError("No 'request' section found in mapping config")

    root_key = request_config.get("root_key", "Payments")
    batch_container = request_config.get("batch_container", "BatchDetails")

    tc_id = transactions[0].get("tc_id", "unknown")
    logger.info("Generating multi-batch request JSON for tc_id=%s (%d batches)",
                tc_id, len(transactions))

    # Generate filename from pattern
    generated_filename = _generate_filename_from_pattern(filename_pattern)

    # Build one batch object per transaction
    batch_array = []
    batch_refs = []
    all_credits = []

    for txn in transactions:
        batch_obj, batch_ref, credits = _build_batch_object(
            txn, request_config, generated_filename=generated_filename
        )
        batch_array.append(batch_obj)
        batch_refs.append(batch_ref)
        all_credits.append(credits)

    # Build the top-level JSON structure (use first transaction for top-level field values)
    first_batch_ref = batch_refs[0] if batch_refs else None
    root_obj = _build_top_level(request_config, transactions[0], first_batch_ref, generated_filename)

    # Place all batch objects in the batch container array
    root_obj[batch_container] = batch_array

    # Build final JSON: {root_key: root_obj}
    result = {root_key: root_obj}

    # Write to file
    if not generated_filename:
        generated_filename = f"{tc_id}_multi.json"
    filepath = os.path.join(output_dir, generated_filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info("Multi-batch request JSON generated: %s (%d batches, refs=%s)",
                filepath, len(batch_array), batch_refs)
    return filepath, batch_refs, all_credits


def _build_batch_object(transaction, request_config, generated_filename=None):
    """Build a single batch object from a transaction.

    Processes debit-level fields and repeating blocks (credits).

    Args:
        transaction: Single transaction dict.
        request_config: The 'request' section of the mapping config.
        generated_filename: Pre-generated filename (for filename-source fields).

    Returns:
        Tuple of (batch_obj_dict, batch_reference, updated_credits_list).
    """
    batch_ref_field = request_config.get("batch_reference_field", "")
    credit_ref_field = request_config.get("credit_reference_field", "")

    # Parse credit_json
    credits = transaction.get("credit_json")
    if isinstance(credits, str):
        credits = json.loads(credits)
    credits = credits or []

    batch_reference = None
    batch_obj = {}

    # Process debit-level fields
    fields = request_config.get("fields", [])

    # Pre-generate batch reference value so batch_ref_copy fields can use it
    if batch_ref_field:
        for field in fields:
            if field.get("json_path") == batch_ref_field and field.get("source") == "auto":
                auto_config = field.get("auto_generate", {})
                batch_reference = generate_value(auto_config)
                logger.debug("Batch reference pre-generated: %s", batch_reference)
                break

    for field in fields:
        json_path = field.get("json_path", "")
        source = field.get("source", "")

        if source == "batch_ref_copy":
            value = batch_reference or ""
        elif json_path == batch_ref_field and source == "auto":
            value = batch_reference
        else:
            value = _resolve_field_value(field, transaction, source, generated_filename=generated_filename)

        _set_json_value(batch_obj, json_path, value)

        # Check if this is the batch reference field
        if json_path == batch_ref_field:
            batch_reference = value
            logger.debug("Batch reference set to: %s", batch_reference)

    # Process repeating blocks (credits)
    repeating_blocks = _get_repeating_blocks(request_config)

    for block in repeating_blocks:
        parent_path = block.get("parent_path", "")
        repeat_element = block.get("repeat_element", "CreditAccount")
        block_fields = block.get("fields", [])
        block_name = block.get("name", "credits")

        # Determine data source for this block
        if block_name == "credits" or not block.get("name"):
            block_data = credits
        else:
            block_data = transaction.get(f"{block_name}_json")
            if isinstance(block_data, str):
                block_data = json.loads(block_data)
            block_data = block_data or []

        if not block_data:
            continue

        # Build array of credit objects
        credit_array = []
        for idx, item in enumerate(block_data):
            credit_obj = {}

            # Pre-generate credit reference value so credit_ref_copy fields can use it
            credit_ref_value = None
            for cf in block_fields:
                if cf.get("json_path") == credit_ref_field and cf.get("source") == "auto":
                    auto_config = cf.get("auto_generate", {})
                    credit_ref_value = generate_value(auto_config)
                    item["credit_reference"] = credit_ref_value
                    logger.debug("%s[%d] reference pre-generated: %s", block_name, idx, credit_ref_value)
                    break

            # Process all fields for this credit
            for cf in block_fields:
                cf_path = cf.get("json_path", "")
                cf_source = cf.get("source", "")

                if cf_source == "credit_ref_copy":
                    cf_value = credit_ref_value or ""
                elif cf_source == "batch_ref_copy":
                    cf_value = batch_reference or ""
                elif cf_path == credit_ref_field and cf_source == "auto":
                    cf_value = credit_ref_value
                else:
                    cf_value = _resolve_credit_field_value(cf, item, cf_source, idx, generated_filename=generated_filename)

                _set_json_value(credit_obj, cf_path, cf_value)

                # If this is the credit reference field (non-auto source), still capture it
                if cf_path == credit_ref_field and credit_ref_value is None:
                    credit_ref_value = cf_value
                    item["credit_reference"] = cf_value
                    logger.debug("%s[%d] reference set to: %s", block_name, idx, cf_value)

            credit_array.append(credit_obj)

        # Place the credit array at the correct path within the batch object
        if parent_path:
            _set_json_value(batch_obj, f"{parent_path}.{repeat_element}", credit_array)
        else:
            _set_json_value(batch_obj, repeat_element, credit_array)

    return batch_obj, batch_reference, credits


def _build_top_level(request_config, transaction, batch_reference, generated_filename):
    """Build the top-level (root_key) fields dict.

    Args:
        request_config: The 'request' section of mapping config.
        transaction: Transaction dict (used for resolving excel-source fields).
        batch_reference: The batch reference value (for batch_ref_copy fields).
        generated_filename: Pre-generated filename.

    Returns:
        Dict of top-level fields.
    """
    top_level_fields = request_config.get("top_level_fields", [])
    root_obj = {}
    for field in top_level_fields:
        json_path = field.get("json_path", "")
        source = field.get("source", "")
        if source == "batch_ref_copy":
            value = batch_reference or ""
        else:
            value = _resolve_field_value(field, transaction, source, generated_filename=generated_filename)
        _set_json_value(root_obj, json_path, value)
    return root_obj


def _resolve_field_value(field, transaction, source, generated_filename=None):
    """Resolve a field value based on its source type."""
    if source == "excel":
        col = field.get("excel_column", "")
        return _get_transaction_value(transaction, col)
    elif source == "auto":
        auto_config = field.get("auto_generate", {})
        return generate_value(auto_config)
    elif source == "hardcoded":
        return field.get("value", "")
    elif source == "filename":
        return os.path.splitext(generated_filename)[0] if generated_filename else ""
    return ""


def _resolve_credit_field_value(field, credit, source, idx, generated_filename=None):
    """Resolve a credit field value from the credit dict."""
    if source == "excel":
        col = field.get("excel_column", "")
        col_map = {
            "credit_account": "account",
            "credit_ifsc": "ifsc",
            "credit_amount": "amount",
            "beneficiary_name": "beneficiary_name",
            "pay_mode": "pay_mode",
        }
        key = col_map.get(col, col)
        return credit.get(key, "")
    elif source == "auto":
        auto_config = field.get("auto_generate", {})
        return generate_value(auto_config)
    elif source == "hardcoded":
        return field.get("value", "")
    elif source == "filename":
        return os.path.splitext(generated_filename)[0] if generated_filename else ""
    return ""


def _get_transaction_value(transaction, excel_column):
    """Get a value from the transaction dict based on Excel column name."""
    col_map = {
        "debit_account": "debit_account",
        "debit_account_parent": "debit_account_parent",
        "debit_ifsc": "debit_ifsc",
        "debit_amount": "debit_amount",
        "credit_count": "credit_count",
        "scheme": "scheme",
        "tcid": "tc_id",
    }
    key = col_map.get(excel_column, excel_column)
    value = transaction.get(key, "")
    if value is None:
        return ""
    return str(value)


def _get_repeating_blocks(config):
    """Get repeating blocks from config, supporting both new and legacy format."""
    blocks = config.get("repeating_blocks", [])
    if blocks:
        return blocks

    credit_block = config.get("credit_block", {})
    if credit_block:
        block = dict(credit_block)
        block.setdefault("name", "credits")
        return [block]

    return []


def _set_json_value(obj, dotted_path, value):
    """Set a value in a nested dict using a dotted path.

    Supports:
    - "key" — simple key
    - "parent.child" — nested object
    - "parent.child[]" — append to array
    - "parent.child[N]" — set at specific array index
    - Direct assignment if value is a list (for credit arrays)

    If value is a list, it's set directly (used for credit arrays).
    """
    if not dotted_path or value is None:
        return

    parts = dotted_path.split(".")
    current = obj

    for i, part in enumerate(parts):
        is_last = (i == len(parts) - 1)

        # Handle array append notation: "key[]"
        if part.endswith("[]"):
            key = part[:-2]
            if is_last:
                if key not in current:
                    current[key] = []
                current[key].append(value)
            else:
                if key not in current:
                    current[key] = []
                # Navigate into last element or create new
                if not current[key]:
                    current[key].append({})
                current = current[key][-1]

        # Handle array index notation: "[N]" (standalone index after a dot)
        elif part.startswith("[") and part.endswith("]"):
            try:
                index = int(part[1:-1])
                # "current" should already be a list (set by previous part)
                if isinstance(current, list):
                    while len(current) <= index:
                        current.append(None)
                    if is_last:
                        current[index] = value
                    else:
                        if current[index] is None:
                            current[index] = {}
                        current = current[index]
                elif isinstance(current, dict):
                    # Fallback: treat as dict key (shouldn't happen with correct paths)
                    if is_last:
                        current[part] = value
                    else:
                        if part not in current:
                            current[part] = {}
                        current = current[part]
            except ValueError:
                # Not a valid index, treat as dict key
                if is_last:
                    current[part] = value
                else:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

        # Handle key with inline array index: "key[N]"
        elif "[" in part and part.endswith("]"):
            bracket_pos = part.index("[")
            key = part[:bracket_pos]
            try:
                index = int(part[bracket_pos + 1:-1])
                if key not in current:
                    current[key] = []
                arr = current[key]
                if not isinstance(arr, list):
                    current[key] = []
                    arr = current[key]
                while len(arr) <= index:
                    arr.append(None)
                if is_last:
                    arr[index] = value
                else:
                    if arr[index] is None:
                        arr[index] = {}
                    current = arr[index]
            except ValueError:
                if is_last:
                    current[part] = value
                else:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

        elif is_last:
            # Set the value — if it's a list, set directly
            current[part] = value
        else:
            # Navigate/create intermediate dict
            if part not in current:
                current[part] = {}
            # If it's already a non-dict (e.g., string), replace with dict
            if not isinstance(current[part], dict) and not isinstance(current[part], list):
                current[part] = {}
            current = current[part]


def _generate_filename_from_pattern(filename_pattern):
    """Generate filename from pattern: PREFIX + DATE + SEQUENCE.json."""
    if not filename_pattern:
        return None
    prefix = filename_pattern.get("prefix", "")
    date_format = filename_pattern.get("date_format", "")

    if not prefix and not date_format:
        return None

    date_str = _format_date(date_format) if date_format else ""
    base = f"{prefix}{date_str}"

    sequence = _get_next_sequence(base)
    return f"{base}{str(sequence).zfill(3)}.json"


def _format_date(fmt):
    """Convert common date format strings (yyyyMMdd, etc.) to formatted date."""
    now = datetime.now()
    py_fmt = (fmt.replace('yyyy', '%Y')
                 .replace('yy', '%y')
                 .replace('MM', '%m')
                 .replace('dd', '%d')
                 .replace('HH', '%H')
                 .replace('mm', '%M')
                 .replace('ss', '%S'))
    return now.strftime(py_fmt)


def _get_next_sequence(base_pattern):
    """Get next sequence number by counting existing filenames with same prefix+date today."""
    from backend.db import get_db
    db = get_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) as cnt FROM transactions "
            "WHERE generated_xml_filename LIKE ? AND DATE(created_at) = DATE('now')",
            (f"{base_pattern}%",)
        ).fetchone()
        return (row[0] if row else 0) + 1
    finally:
        db.close()
