"""Generate request XML files from mapping config and transaction data.

Uses xml.etree.ElementTree to build XMLs programmatically.
Supports attribute-based XML structures where data is in element attributes.
"""
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from xml.dom import minidom

from backend.services.auto_generator import generate_value

logger = logging.getLogger(__name__)


def _strip_ns(tag):
    """Remove namespace URI from an element tag."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def generate_request_xml(transaction, mapping_config, output_dir, filename_pattern=None):
    """Generate a single request XML file for a transaction.

    Args:
        transaction: dict with keys from DB (debit_account, credit_json, etc.)
        mapping_config: The full mapping config dict from the scheme.
        output_dir: Directory to write the XML file.
        filename_pattern: Optional dict with 'prefix' and 'date_format' for filename generation.

    Returns:
        Tuple of (xml_file_path, batch_reference, updated_credit_json).
    """
    request_config = mapping_config.get("request", {})
    if not request_config:
        raise ValueError("No 'request' section found in mapping config")

    root_element = request_config.get("root_element", "PaymentRequest")
    debit_element_name = request_config.get("debit_element", "Debit_Account")
    batch_ref_field = request_config.get("batch_reference_field", "")
    credit_ref_field = request_config.get("credit_reference_field", "")

    logger.info("Generating request XML for transaction id=%s, tc_id=%s",
                transaction.get("id"), transaction.get("tc_id"))

    # Parse credit_json
    credits = transaction.get("credit_json")
    if isinstance(credits, str):
        credits = json.loads(credits)
    credits = credits or []

    # Build XML tree — xmlns is handled as a regular attribute via field mappings
    root = ET.Element(root_element)

    # Track generated values for batch/credit references
    batch_reference = None
    generated_values = {}

    # Generate filename from pattern (computed early so it can be injected into XML fields)
    generated_filename = _generate_filename_from_pattern(filename_pattern)

    # Process debit-level fields
    fields = request_config.get("fields", [])

    # Pre-generate batch reference value so batch_ref_copy fields can use it
    if batch_ref_field:
        for field in fields:
            if field.get("xml_path") == batch_ref_field and field.get("source") == "auto":
                auto_config = field.get("auto_generate", {})
                batch_reference = generate_value(auto_config)
                logger.debug("Batch reference pre-generated: %s", batch_reference)
                break

    for field in fields:
        xml_path = field.get("xml_path", "")
        source = field.get("source", "")

        if source == "batch_ref_copy":
            # Use the pre-generated batch reference value
            value = batch_reference or ""
        elif xml_path == batch_ref_field and source == "auto":
            # Use the pre-generated value instead of generating again
            value = batch_reference
        else:
            value = _resolve_field_value(field, transaction, source, generated_filename=generated_filename)

        # Track auto-generated values
        if source == "auto" and field.get("auto_generate"):
            if xml_path not in generated_values:
                generated_values[xml_path] = value

        _set_xml_value(root, xml_path, value)

        # Check if this is the batch reference field
        if xml_path == batch_ref_field:
            batch_reference = value
            logger.debug("Batch reference set to: %s", batch_reference)

    # Process repeating blocks (supports multiple blocks + backward compat with credit_block)
    repeating_blocks = _get_repeating_blocks(request_config)

    for block in repeating_blocks:
        parent_path = block.get("parent_path", "")
        repeat_element = block.get("repeat_element", "Credit_Account")
        block_fields = block.get("fields", [])
        block_name = block.get("name", "credits")

        # Determine data source for this block
        if block_name == "credits" or not block.get("name"):
            block_data = credits
        else:
            # For non-credit blocks, data could come from transaction extras
            block_data = transaction.get(f"{block_name}_json")
            if isinstance(block_data, str):
                block_data = json.loads(block_data)
            block_data = block_data or []

        if not block_data:
            continue

        # Create parent element(s) from path
        parent = _ensure_element_path(root, parent_path)

        for idx, item in enumerate(block_data):
            elem = ET.SubElement(parent, repeat_element)

            # Pre-generate credit reference value so credit_ref_copy fields can use it
            credit_ref_value = None
            for cf in block_fields:
                if cf.get("xml_path") == credit_ref_field and cf.get("source") == "auto":
                    auto_config = cf.get("auto_generate", {})
                    credit_ref_value = generate_value(auto_config)
                    item["credit_reference"] = credit_ref_value
                    logger.debug("%s[%d] reference pre-generated: %s", block_name, idx, credit_ref_value)
                    break

            # Single pass: process all fields in original order (preserves sibling sequence)
            for cf in block_fields:
                cf_path = cf.get("xml_path", "")
                cf_source = cf.get("source", "")

                if cf_source == "credit_ref_copy":
                    cf_value = credit_ref_value or ""
                elif cf_source == "batch_ref_copy":
                    cf_value = batch_reference or ""
                elif cf_path == credit_ref_field and cf_source == "auto":
                    # Use the pre-generated value instead of generating again
                    cf_value = credit_ref_value
                else:
                    cf_value = _resolve_credit_field_value(cf, item, cf_source, idx, generated_filename=generated_filename)

                _set_relative_value(elem, cf_path, cf_value)

                # If this is the credit reference field (non-auto source), still capture it
                if cf_path == credit_ref_field and credit_ref_value is None:
                    credit_ref_value = cf_value
                    item["credit_reference"] = cf_value
                    logger.debug("%s[%d] reference set to: %s", block_name, idx, cf_value)

    # Generate XML string
    xml_str = _pretty_xml(root)

    # Write to file — use pattern-based filename if configured, otherwise default
    if not generated_filename:
        tc_id = transaction.get("tc_id", "unknown")
        batch_ref_safe = batch_reference or "nobatch"
        generated_filename = f"{tc_id}_{batch_ref_safe}.xml"
    filepath = os.path.join(output_dir, generated_filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(xml_str)

    logger.info("Request XML generated: %s (batch_ref=%s)", filepath, batch_reference)
    return filepath, batch_reference, credits


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
        # Map Excel column names to credit dict keys
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
    """Get repeating blocks from config, supporting both new and legacy format.

    New format: config["repeating_blocks"] = [{name, parent_path, repeat_element, fields}, ...]
    Legacy format: config["credit_block"] = {parent_path, repeat_element, fields}

    Returns list of block dicts.
    """
    blocks = config.get("repeating_blocks", [])
    if blocks:
        return blocks

    # Backward compatibility: convert single credit_block to list
    credit_block = config.get("credit_block", {})
    if credit_block:
        block = dict(credit_block)
        block.setdefault("name", "credits")
        return [block]

    return []


def _navigate_or_create(current, segment):
    """Navigate to or create a child element, respecting optional [N] index.

    - 'Transform' → find-or-create single element
    - 'Transform[0]' → ensure at least 1 Transform child, return first
    - 'Transform[1]' → ensure at least 2 Transform children, return second
    """
    m = re.match(r'^(.+)\[(\d+)\]$', segment)
    if m:
        tag = m.group(1)
        idx = int(m.group(2))
        existing = current.findall(tag)
        while len(existing) <= idx:
            existing.append(ET.SubElement(current, tag))
        return existing[idx]
    else:
        child = current.find(segment)
        if child is None:
            child = ET.SubElement(current, segment)
        return child


def _strip_index(segment):
    """Strip [N] index suffix from a path segment. 'Transform[1]' → 'Transform'."""
    return re.sub(r'\[\d+\]$', '', segment)


def _set_relative_value(elem, rel_path, value):
    """Set a value on an element using a relative path (inside a repeating block).

    Supports:
    - @Attribute — attribute on the element
    - RmtInf/C7495 — nested text content
    - RmtInf/@attr — nested attribute
    - C7002 — direct child text content
    - C7495[0], C7495[1] — indexed repeated leaf siblings
    - SubElem[0]/@attr, SubElem[1]/@attr — indexed attribute-only siblings
    """
    if not rel_path or value is None:
        return

    if rel_path.startswith("@") or _strip_index(rel_path).startswith("@"):
        attr = _strip_index(rel_path)
        elem.set(attr[1:], str(value))
    elif "/" in rel_path:
        parts = rel_path.split("/")
        current = elem
        for p in parts[:-1]:
            current = _navigate_or_create(current, p)
        last = parts[-1]
        if last.startswith("@") or _strip_index(last).startswith("@"):
            current.set(_strip_index(last)[1:], str(value))
        else:
            tag = _strip_index(last)
            sub = ET.SubElement(current, tag)
            sub.text = str(value)
    else:
        tag = _strip_index(rel_path)
        sub = ET.SubElement(elem, tag)
        sub.text = str(value)


def _set_xml_value(root, xml_path, value):
    """Set a value in the XML tree using a full path like 'Element/Child/@attribute'.

    Supports full XPath-style paths from root. Skips the root element name
    if it appears as the first path component.

    For text-content paths (no trailing @attr), the last element is always
    created as a new child (ET.SubElement) to support repeating sibling
    elements (e.g., multiple <C7002> under <FileOrgtr>). Intermediate
    elements are found-or-created as before.
    """
    if not xml_path or value is None:
        return

    parts = [p for p in xml_path.split("/") if p]

    # Skip root element name if it appears first (compare stripped tag)
    if parts and _strip_index(parts[0]) == _strip_ns(root.tag):
        parts = parts[1:]

    if not parts:
        return

    current = root

    # Check if last part is an attribute
    if _strip_index(parts[-1]).startswith("@"):
        attr_name = _strip_index(parts[-1])[1:]
        element_parts = parts[:-1]

        # Navigate/create intermediate elements (index-aware)
        for part in element_parts:
            current = _navigate_or_create(current, part)

        current.set(attr_name, str(value))
    else:
        # Text-content path: intermediate elements use index-aware navigation,
        # last element is always created new (supports repeating siblings)
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                # Last element: always create new for repeating sibling support
                tag = _strip_index(part)
                child = ET.SubElement(current, tag)
            else:
                # Intermediate: index-aware find or create
                child = _navigate_or_create(current, part)
            current = child

        current.text = str(value)


def _ensure_element_path(root, path):
    """Ensure a nested element path exists, creating elements as needed.

    Supports full paths from root. Skips the root element name if it
    appears as the first path component.
    """
    if not path:
        return root

    parts = [p for p in path.split("/") if p]

    # Skip root element name if it appears first (compare stripped tag)
    if parts and _strip_index(parts[0]) == _strip_ns(root.tag):
        parts = parts[1:]

    current = root
    for part in parts:
        current = _navigate_or_create(current, part)

    return current


def _pretty_xml(element):
    """Convert an ElementTree element to a pretty-printed XML string."""
    rough = ET.tostring(element, encoding="unicode", xml_declaration=False)
    parsed = minidom.parseString(rough)
    pretty = parsed.toprettyxml(indent="    ", encoding=None)
    # Remove the XML declaration added by minidom (we'll add our own)
    lines = pretty.split("\n")
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    xml_body = "\n".join(line for line in lines if line.strip())
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n"


def _generate_filename_from_pattern(filename_pattern):
    """Generate filename from pattern: PREFIX + DATE + SEQUENCE.xml.

    Args:
        filename_pattern: dict with 'prefix' and 'date_format' keys, or None.

    Returns None if no filename pattern is configured.
    """
    if not filename_pattern:
        return None
    prefix = filename_pattern.get("prefix", "")
    date_format = filename_pattern.get("date_format", "")

    if not prefix and not date_format:
        return None

    date_str = _format_date(date_format) if date_format else ""
    base = f"{prefix}{date_str}"

    # Get next sequence number by querying transactions with matching filename today
    sequence = _get_next_sequence(base)
    return f"{base}{str(sequence).zfill(3)}.xml"


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
