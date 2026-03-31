"""Parse initiation and response JSON files/payloads using mapping config.

Extracts field values based on the scheme's mapping configuration.
Also provides JSON-to-tree conversion for the mapping UI.
"""
import json
import logging

logger = logging.getLogger(__name__)


def parse_json_file(file_path, json_type, mapping_config):
    """Parse a JSON file (initiation or response) using mapping config.

    Args:
        file_path: Path to the JSON file.
        json_type: 'initiation', 'response', or 'response_fail'.
        mapping_config: Full mapping config dict from the scheme.

    Returns:
        Dict with parsed data:
        {
            "batch_reference": "BATCH...",
            "debit": {"debit_account": "...", "debit_amount": "...", ...},
            "credits": [
                {"reference": "...", "account": "...", "amount": "...", "status": "..."},
                ...
            ]
        }
    """
    logger.info("Parsing %s JSON file: %s", json_type, file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return parse_json_data(data, json_type, mapping_config)


def parse_json_data(data, json_type, mapping_config):
    """Parse a JSON object (initiation or response) using mapping config.

    Args:
        data: Parsed JSON dict.
        json_type: 'initiation', 'response', or 'response_fail'.
        mapping_config: Full mapping config dict from the scheme.

    Returns:
        Same structure as parse_json_file.
    """
    config = mapping_config.get(json_type, {})
    if not config:
        raise ValueError(f"No '{json_type}' section found in mapping config")

    root_key = config.get("root_key", "")
    batch_container = config.get("batch_container", "")

    # Navigate to the root object
    root_obj = data
    if root_key and root_key in data:
        root_obj = data[root_key]

    # Navigate to the batch container (array of batch objects)
    batches = []
    if batch_container and batch_container in root_obj:
        container = root_obj[batch_container]
        if isinstance(container, list):
            batches = container
        else:
            batches = [container]
    else:
        # If no batch container, treat the root object as a single batch
        batches = [root_obj]

    # Process the first batch (typical case for matched files)
    if not batches:
        logger.warning("No batch entries found in JSON for %s", json_type)
        return {"batch_reference": "", "debit": {}, "credits": []}

    batch_obj = batches[0]

    # Extract batch reference
    batch_ref_field = config.get("batch_reference_field", "")
    batch_reference = _get_json_value(batch_obj, batch_ref_field) if batch_ref_field else ""
    logger.debug("Extracted batch reference: %s (from %s)", batch_reference, batch_ref_field)

    # Extract debit-level fields
    debit_data = {}
    fields = config.get("fields", [])
    for field in fields:
        json_path = field.get("json_path", "")
        map_to = field.get("map_to", "")
        if map_to:
            value = _get_json_value(batch_obj, json_path)
            debit_data[map_to] = value if value is not None else ""
            logger.debug("Debit field %s = %s (from %s)", map_to, value, json_path)

    # Extract repeating block entries (credits)
    repeating_blocks = _get_repeating_blocks(config)
    credit_ref_field = config.get("credit_reference_field", "")
    all_block_data = {}
    credits = []

    for block in repeating_blocks:
        parent_path = block.get("parent_path", "")
        repeat_element = block.get("repeat_element", "CreditAccount")
        block_fields = block.get("fields", [])
        block_name = block.get("name", "credits")

        # Find the parent object
        parent = _get_json_value(batch_obj, parent_path) if parent_path else batch_obj
        if parent is None:
            parent = batch_obj

        # Get the repeating array
        elements = []
        if isinstance(parent, dict) and repeat_element in parent:
            elem = parent[repeat_element]
            if isinstance(elem, list):
                elements = elem
            else:
                elements = [elem]

        logger.debug("Found %d '%s' elements at %s.%s",
                     len(elements), repeat_element, parent_path, repeat_element)

        block_entries = []
        for idx, elem in enumerate(elements):
            entry_data = {}
            for cf in block_fields:
                cf_path = cf.get("json_path", "")
                cf_map_to = cf.get("map_to", "")
                if cf_map_to:
                    value = _get_json_value(elem, cf_path) if isinstance(elem, dict) else ""
                    entry_data[cf_map_to] = value if value is not None else ""
                    logger.debug("%s[%d] %s = %s", block_name, idx, cf_map_to, value)

            # Fallback: extract credit reference if not mapped explicitly
            if not entry_data.get("reference") and credit_ref_field and block_name == "credits":
                ref_value = _get_json_value(elem, credit_ref_field) if isinstance(elem, dict) else ""
                if ref_value:
                    entry_data["reference"] = ref_value
                    logger.debug("%s[%d] reference auto-extracted via credit_reference_field '%s' = %s",
                                 block_name, idx, credit_ref_field, ref_value)

            block_entries.append(entry_data)

        all_block_data[block_name] = block_entries
        if block_name == "credits" or not credits:
            credits = block_entries

    result = {
        "batch_reference": batch_reference,
        "debit": debit_data,
        "credits": credits,
        "repeating_blocks": all_block_data
    }

    logger.info("Parsed %s JSON: batch_ref=%s, credits=%d, blocks=%d",
                json_type, batch_reference, len(credits), len(all_block_data))
    return result


def extract_batch_reference(file_path, json_type, mapping_config):
    """Extract only the batch reference from a JSON file (for file matching).

    Args:
        file_path: Path to the JSON file.
        json_type: 'initiation' or 'response'.
        mapping_config: Full mapping config dict.

    Returns:
        The batch reference string, or None if not found.
    """
    config = mapping_config.get(json_type, {})
    batch_ref_field = config.get("batch_reference_field", "")

    if not batch_ref_field:
        logger.warning("No batch_reference_field configured for %s", json_type)
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        root_key = config.get("root_key", "")
        batch_container = config.get("batch_container", "")

        root_obj = data
        if root_key and root_key in data:
            root_obj = data[root_key]

        # Navigate to batch container
        if batch_container and batch_container in root_obj:
            container = root_obj[batch_container]
            if isinstance(container, list) and container:
                batch_obj = container[0]
            elif isinstance(container, dict):
                batch_obj = container
            else:
                return None
        else:
            batch_obj = root_obj

        batch_ref = _get_json_value(batch_obj, batch_ref_field)
        logger.debug("Extracted batch ref from %s: %s", file_path, batch_ref)
        return batch_ref

    except json.JSONDecodeError:
        logger.error("Failed to parse JSON file: %s", file_path)
        return None
    except Exception:
        logger.exception("Error extracting batch reference from %s", file_path)
        return None


def extract_batch_references_multi(file_path, json_type, mapping_config):
    """Extract batch references from ALL batches in a JSON file.

    Used when a single JSON file contains multiple BatchDetails entries.

    Returns:
        List of (batch_reference, batch_index) tuples.
    """
    config = mapping_config.get(json_type, {})
    batch_ref_field = config.get("batch_reference_field", "")
    if not batch_ref_field:
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        root_key = config.get("root_key", "")
        batch_container = config.get("batch_container", "")

        root_obj = data
        if root_key and root_key in data:
            root_obj = data[root_key]

        batches = []
        if batch_container and batch_container in root_obj:
            container = root_obj[batch_container]
            if isinstance(container, list):
                batches = container
            else:
                batches = [container]
        else:
            batches = [root_obj]

        result = []
        for idx, batch_obj in enumerate(batches):
            ref = _get_json_value(batch_obj, batch_ref_field)
            if ref:
                result.append((ref, idx))

        return result

    except Exception:
        logger.exception("Error extracting batch references from %s", file_path)
        return []


def parse_multi_batch_json(data, json_type, mapping_config):
    """Parse a multi-batch JSON, returning one result per batch entry.

    Used when a single JSON file contains multiple BatchDetails entries.

    Args:
        data: Parsed JSON dict (or file path string).
        json_type: 'initiation', 'response', or 'response_fail'.
        mapping_config: Full mapping config dict from the scheme.

    Returns:
        List of dicts, one per batch:
        [
            {"batch_reference": "B001", "debit": {...}, "credits": [...]},
            {"batch_reference": "B002", "debit": {...}, "credits": [...]}
        ]
    """
    if isinstance(data, str):
        # It's a file path
        with open(data, "r", encoding="utf-8") as f:
            data = json.load(f)

    config = mapping_config.get(json_type, {})
    if not config:
        raise ValueError(f"No '{json_type}' section found in mapping config")

    root_key = config.get("root_key", "")
    batch_container = config.get("batch_container", "")

    # Navigate to the root object
    root_obj = data
    if root_key and root_key in data:
        root_obj = data[root_key]

    # Get all batch entries
    batches = []
    if batch_container and batch_container in root_obj:
        container = root_obj[batch_container]
        if isinstance(container, list):
            batches = container
        else:
            batches = [container]
    else:
        batches = [root_obj]

    if not batches:
        logger.warning("No batch entries found in multi-batch JSON for %s", json_type)
        return []

    batch_ref_field = config.get("batch_reference_field", "")
    credit_ref_field = config.get("credit_reference_field", "")
    fields = config.get("fields", [])
    repeating_blocks = _get_repeating_blocks(config)

    results = []
    for batch_idx, batch_obj in enumerate(batches):
        # Extract batch reference
        batch_reference = _get_json_value(batch_obj, batch_ref_field) if batch_ref_field else ""
        logger.debug("Multi-batch[%d] batch_ref=%s", batch_idx, batch_reference)

        # Extract debit-level fields
        debit_data = {}
        for field in fields:
            json_path = field.get("json_path", "")
            map_to = field.get("map_to", "")
            if map_to:
                value = _get_json_value(batch_obj, json_path)
                debit_data[map_to] = value if value is not None else ""

        # Extract repeating block entries (credits)
        all_block_data = {}
        credits = []

        for block in repeating_blocks:
            parent_path = block.get("parent_path", "")
            repeat_element = block.get("repeat_element", "CreditAccount")
            block_fields = block.get("fields", [])
            block_name = block.get("name", "credits")

            parent = _get_json_value(batch_obj, parent_path) if parent_path else batch_obj
            if parent is None:
                parent = batch_obj

            elements = []
            if isinstance(parent, dict) and repeat_element in parent:
                elem = parent[repeat_element]
                if isinstance(elem, list):
                    elements = elem
                else:
                    elements = [elem]

            block_entries = []
            for idx, elem in enumerate(elements):
                entry_data = {}
                for cf in block_fields:
                    cf_path = cf.get("json_path", "")
                    cf_map_to = cf.get("map_to", "")
                    if cf_map_to:
                        value = _get_json_value(elem, cf_path) if isinstance(elem, dict) else ""
                        entry_data[cf_map_to] = value if value is not None else ""

                # Fallback credit reference extraction
                if not entry_data.get("reference") and credit_ref_field and block_name == "credits":
                    ref_value = _get_json_value(elem, credit_ref_field) if isinstance(elem, dict) else ""
                    if ref_value:
                        entry_data["reference"] = ref_value

                block_entries.append(entry_data)

            all_block_data[block_name] = block_entries
            if block_name == "credits" or not credits:
                credits = block_entries

        results.append({
            "batch_reference": batch_reference,
            "batch_index": batch_idx,
            "debit": debit_data,
            "credits": credits,
            "repeating_blocks": all_block_data
        })

    logger.info("Parsed multi-batch %s JSON: %d batches", json_type, len(results))
    return results


def parse_json_to_tree(json_content):
    """Parse JSON content and return a tree structure for the mapping UI.

    Args:
        json_content: JSON string content.

    Returns:
        Nested dict representing the JSON tree:
        {
            "key": "Payments",
            "type": "object",
            "path": "Payments",
            "children": [
                {
                    "key": "BatchDetails",
                    "type": "array",
                    "path": "Payments.BatchDetails",
                    "children": [...]  // first element as representative
                },
                {
                    "key": "MessageId",
                    "type": "string",
                    "path": "Payments.MessageId",
                    "value": "024PAOPAYREQ..."
                }
            ]
        }
    """
    logger.info("Parsing JSON content to tree structure for mapping UI")

    if isinstance(json_content, str):
        data = json.loads(json_content)
    else:
        data = json_content

    # JSON must be an object at top level
    if not isinstance(data, dict):
        raise ValueError("JSON content must be an object at the top level")

    # Build tree from the top-level keys
    # Typically there's one root key like "Payments"
    keys = list(data.keys())
    if len(keys) == 1:
        # Single root key — make it the root node
        root_key = keys[0]
        root_val = data[root_key]
        tree = _value_to_tree(root_key, root_val, root_key)
        return tree
    else:
        # Multiple top-level keys — wrap in a virtual root
        tree = {
            "key": "(root)",
            "type": "object",
            "path": "",
            "children": []
        }
        for key in keys:
            child = _value_to_tree(key, data[key], key)
            tree["children"].append(child)
        return tree


def _value_to_tree(key, value, path):
    """Recursively convert a JSON value to a tree node."""
    if isinstance(value, dict):
        node = {
            "key": key,
            "type": "object",
            "path": path,
            "children": []
        }
        for k, v in value.items():
            child_path = f"{path}.{k}" if path else k
            node["children"].append(_value_to_tree(k, v, child_path))
        return node

    elif isinstance(value, list):
        node = {
            "key": key,
            "type": "array",
            "path": path,
            "children": [],
            "item_count": len(value)
        }

        if not value:
            return node

        first = value[0]
        if isinstance(first, dict):
            # Array of objects — show first element's keys as representative
            for k, v in first.items():
                child_path = f"{path}.{k}" if path else k
                node["children"].append(_value_to_tree(k, v, child_path))
        elif isinstance(first, (str, int, float)):
            # Array of scalars — show indexed items
            for idx, item in enumerate(value):
                child_path = f"{path}[{idx}]"
                node["children"].append({
                    "key": f"[{idx}]",
                    "type": type(item).__name__,
                    "path": child_path,
                    "value": str(item)
                })
        return node

    else:
        # Scalar value
        return {
            "key": key,
            "type": type(value).__name__ if value is not None else "null",
            "path": path,
            "value": str(value) if value is not None else ""
        }


def _get_json_value(obj, dotted_path):
    """Get a value from a nested dict using a dotted path.

    Supports:
    - "key" — simple key lookup
    - "parent.child" — nested lookup
    - "array[0]" — indexed array access
    - "parent.child[0].key" — mixed access

    Returns the value (could be str, int, dict, list) or None if not found.
    """
    if not dotted_path or obj is None:
        return None

    # Split path by dots, but respect array indices
    parts = _split_json_path(dotted_path)
    current = obj

    for part in parts:
        if current is None:
            return None

        # Check for array index: key[N]
        idx_match = _parse_array_index(part)
        if idx_match:
            key, index = idx_match
            if key:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return None
            if isinstance(current, list) and index < len(current):
                current = current[index]
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None

    # Convert to string for consistency (if scalar)
    if isinstance(current, (str, int, float, bool)):
        return str(current)
    return current


def _split_json_path(path):
    """Split a dotted JSON path into parts, respecting array indices.

    Examples:
        "Payments.BatchDetails" -> ["Payments", "BatchDetails"]
        "BatchDetails[0].CorporateId" -> ["BatchDetails[0]", "CorporateId"]
        "DebitAccounts.DebitAccount.C2020" -> ["DebitAccounts", "DebitAccount", "C2020"]
    """
    return path.split(".")


def _parse_array_index(part):
    """Parse array index from a path part like 'key[0]' or '[0]'.

    Returns (key, index) tuple or None if not an indexed access.
    """
    import re
    m = re.match(r'^(.+?)\[(\d+)\]$', part)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r'^\[(\d+)\]$', part)
    if m:
        return None, int(m.group(1))
    return None


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


def check_success_indicator(data, config):
    """Check if a JSON response is a success or failure based on configured indicator.

    For split response mode, checks a configured JSON path for a specific value
    to classify the response.

    Args:
        data: Parsed JSON dict.
        config: Response mapping config with success_indicator_path and success_indicator_value.

    Returns:
        'response' if success indicator matches, 'response_fail' otherwise.
    """
    indicator_path = config.get("success_indicator_path", "")
    indicator_value = config.get("success_indicator_value", "")

    # Fallback to legacy tag-based check
    if not indicator_path:
        indicator_path = config.get("success_indicator_tag", "")
        if not indicator_path:
            return "response"

    root_key = config.get("root_key", "")
    root_obj = data
    if root_key and root_key in data:
        root_obj = data[root_key]

    # Navigate to batch container first
    batch_container = config.get("batch_container", "")
    if batch_container and batch_container in root_obj:
        container = root_obj[batch_container]
        if isinstance(container, list) and container:
            check_obj = container[0]
        else:
            check_obj = container if isinstance(container, dict) else root_obj
    else:
        check_obj = root_obj

    # Check if the indicator path exists and optionally matches a value
    found_value = _get_json_value(check_obj, indicator_path)

    if indicator_value:
        # Value-based check
        if found_value is not None and str(found_value) == str(indicator_value):
            return "response"
        return "response_fail"
    else:
        # Presence-based check (key exists and is not empty)
        if found_value is not None and found_value != "":
            return "response"
        return "response_fail"
