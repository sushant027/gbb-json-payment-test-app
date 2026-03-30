"""Parse initiation and response XML files using mapping config.

Extracts field values based on the scheme's mapping configuration.
Supports attribute-based XML structures.
"""
import json
import logging
import re
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


def _strip_ns(tag):
    """Remove namespace URI from an element tag. '{http://...}Name' → 'Name'."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def _strip_all_ns(root):
    """Strip namespace from all element tags in the tree (in-place)."""
    for elem in root.iter():
        elem.tag = _strip_ns(elem.tag)
    return root


def parse_xml_file(file_path, xml_type, mapping_config):
    """Parse an XML file (initiation or response) using mapping config.

    Args:
        file_path: Path to the XML file.
        xml_type: 'initiation' or 'response'.
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
    config = mapping_config.get(xml_type, {})
    if not config:
        raise ValueError(f"No '{xml_type}' section found in mapping config")

    logger.info("Parsing %s XML file: %s", xml_type, file_path)

    tree = ET.parse(file_path)
    root = tree.getroot()
    _strip_all_ns(root)

    logger.debug("XML root tag: %s", root.tag)

    # Extract batch reference
    batch_ref_field = config.get("batch_reference_field", "")
    batch_reference = _get_xml_value(root, batch_ref_field)
    logger.debug("Extracted batch reference: %s (from %s)", batch_reference, batch_ref_field)

    # Extract debit-level fields
    debit_data = {}
    fields = config.get("fields", [])
    for field in fields:
        xml_path = field.get("xml_path", "")
        map_to = field.get("map_to", "")
        if map_to:
            value = _get_xml_value(root, xml_path)
            debit_data[map_to] = value
            logger.debug("Debit field %s = %s (from %s)", map_to, value, xml_path)

    # Extract repeating block entries (supports multiple blocks + backward compat)
    repeating_blocks = _get_repeating_blocks(config)
    credit_ref_field = config.get("credit_reference_field", "")
    all_block_data = {}
    credits = []  # Primary credits block for backward compat

    for block in repeating_blocks:
        parent_path = block.get("parent_path", "")
        repeat_element = block.get("repeat_element", "Credit_Account")
        block_fields = block.get("fields", [])
        block_name = block.get("name", "credits")

        # Find the parent element
        parent = _find_element(root, parent_path) if parent_path else root

        block_entries = []
        if parent is not None:
            elements = parent.findall(repeat_element)
            logger.debug("Found %d '%s' elements at %s/%s",
                         len(elements), repeat_element, parent_path, repeat_element)

            for idx, elem in enumerate(elements):
                entry_data = {}
                for cf in block_fields:
                    cf_path = cf.get("xml_path", "")
                    cf_map_to = cf.get("map_to", "")
                    if cf_map_to:
                        value = _get_relative_value(elem, cf_path)
                        entry_data[cf_map_to] = value
                        logger.debug("%s[%d] %s = %s", block_name, idx, cf_map_to, value)

                # Fallback: if "reference" is missing/empty and credit_reference_field
                # is configured, use it to extract the credit reference
                if not entry_data.get("reference") and credit_ref_field and block_name == "credits":
                    ref_value = _get_relative_value(elem, credit_ref_field)
                    if ref_value:
                        entry_data["reference"] = ref_value
                        logger.debug("%s[%d] reference auto-extracted via credit_reference_field '%s' = %s",
                                     block_name, idx, credit_ref_field, ref_value)

                block_entries.append(entry_data)
        else:
            logger.warning("Parent element '%s' not found in XML for block '%s'",
                           parent_path, block_name)
            # Log actual XML structure to help diagnose mapping config mismatch
            root_children = [c.tag for c in root]
            logger.warning("Root element: '%s', direct children: %s", root.tag, root_children)
            if parent_path and '/' in parent_path:
                first_part = parent_path.split('/')[0]
                first_elem = root.find(f"./{first_part}")
                if first_elem is not None:
                    sub_children = [c.tag for c in first_elem]
                    logger.warning("Children of '%s': %s", first_part, sub_children)

        all_block_data[block_name] = block_entries

        # First block or block named "credits" becomes the primary credits
        if block_name == "credits" or not credits:
            credits = block_entries

    result = {
        "batch_reference": batch_reference,
        "debit": debit_data,
        "credits": credits,
        "repeating_blocks": all_block_data
    }

    logger.info("Parsed %s XML: batch_ref=%s, credits=%d, blocks=%d",
                xml_type, batch_reference, len(credits), len(all_block_data))
    return result


def extract_batch_reference(file_path, xml_type, mapping_config):
    """Extract only the batch reference from an XML file (for file matching).

    Args:
        file_path: Path to the XML file.
        xml_type: 'initiation' or 'response'.
        mapping_config: Full mapping config dict.

    Returns:
        The batch reference string, or None if not found.
    """
    config = mapping_config.get(xml_type, {})
    batch_ref_field = config.get("batch_reference_field", "")

    if not batch_ref_field:
        logger.warning("No batch_reference_field configured for %s", xml_type)
        return None

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        _strip_all_ns(root)
        batch_ref = _get_xml_value(root, batch_ref_field)
        logger.debug("Extracted batch ref from %s: %s", file_path, batch_ref)
        return batch_ref
    except ET.ParseError:
        logger.error("Failed to parse XML file: %s", file_path)
        return None
    except Exception:
        logger.exception("Error extracting batch reference from %s", file_path)
        return None


def parse_xml_to_tree(xml_content):
    """Parse XML content and return a tree structure for the mapping UI.

    Args:
        xml_content: XML string content.

    Returns:
        Nested dict representing the XML tree with elements and attributes:
        {
            "tag": "PFTS_Payment_INP",
            "attributes": {},
            "children": [
                {
                    "tag": "Debit_Account",
                    "attributes": {"IFSC_CODE_DEBIT": "...", ...},
                    "children": [...]
                }
            ]
        }
    """
    logger.info("Parsing XML content to tree structure for mapping UI")

    root = ET.fromstring(xml_content)
    tree = _element_to_tree(root)
    return tree


def _element_to_tree(elem):
    """Recursively convert an XML element to a tree dict."""
    attrs = dict(elem.attrib)
    # ElementTree strips xmlns from attrib — re-inject it from the tag
    if elem.tag.startswith("{"):
        ns = elem.tag.split("}", 1)[0][1:]
        attrs["xmlns"] = ns

    node = {
        "tag": _strip_ns(elem.tag),
        "attributes": attrs,
        "children": [],
        "text": (elem.text or "").strip()
    }
    for child in elem:
        node["children"].append(_element_to_tree(child))
    return node


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


def _get_relative_value(elem, rel_path):
    """Get a value from an element using a relative path (inside a repeating block).

    Supports:
    - @Attribute — attribute on the element
    - RmtInf/C7495 — nested text content
    - RmtInf/@attr — nested attribute
    - C7002 — direct child text content
    - C7495[0], C7495[1] — indexed repeated leaf siblings
    """
    if not rel_path:
        return ""

    # Handle indexed paths like C7495[0], RmtInf/C7495[1]
    idx_match = re.match(r'^(.+)\[(\d+)\]$', rel_path)
    if idx_match:
        base_path = idx_match.group(1)
        index = int(idx_match.group(2))
        if base_path.startswith("@"):
            return elem.get(base_path[1:], "")  # attributes don't have indices
        elements = elem.findall(base_path)
        if index < len(elements):
            return (elements[index].text or "").strip()
        return ""

    if rel_path.startswith("@"):
        return elem.get(rel_path[1:], "")
    elif "/" in rel_path:
        parts = rel_path.split("/")
        if parts[-1].startswith("@"):
            # Nested attribute: navigate to parent, get attribute
            elem_path = "/".join(parts[:-1])
            child = elem.find(elem_path)
            if child is None:
                return ""
            return child.get(parts[-1][1:], "")
        else:
            # Nested text content
            child = elem.find(rel_path)
            return (child.text or "").strip() if child is not None else ""
    else:
        child = elem.find(rel_path)
        return (child.text or "").strip() if child is not None else ""


def _get_xml_value(root, path):
    """Get a value from XML using a path like 'Element/@attribute' or 'Element/Child'.

    Supports full paths from root (e.g., 'Debit_Account/CreditAccounts/@attr')
    by using ElementTree's XPath-like find(). Skips the root element name if it
    appears as the first path component.
    """
    if not path:
        return ""

    # Split into element path and optional trailing attribute
    parts = path.split("/")

    # Skip root element name if it appears first
    if parts and parts[0] == root.tag:
        parts = parts[1:]

    if not parts:
        return (root.text or "").strip()

    # Check if the last part is an attribute reference
    if parts[-1].startswith("@"):
        attr_name = parts[-1][1:]
        element_path = "/".join(parts[:-1])

        if element_path:
            # Use XPath-style find for the full element path
            elem = root.find(f"./{element_path}")
            if elem is None:
                logger.debug("Element path '%s' not found for attribute @%s", element_path, attr_name)
                return ""
            return elem.get(attr_name, "")
        else:
            # Attribute directly on root
            return root.get(attr_name, "")
    else:
        # Full element path — find the element and return its text
        element_path = "/".join(parts)
        elem = root.find(f"./{element_path}")
        if elem is None:
            logger.debug("Element path './%s' not found", element_path)
            return ""
        return (elem.text or "").strip()


def _find_element(root, path):
    """Find an element by path using XPath-style navigation.

    Supports full paths from root (e.g., 'Debit_Account/CreditAccounts').
    Skips the root element name if it appears as the first path component.
    """
    if not path:
        return root

    parts = path.split("/")

    # Skip root element name if it appears first
    if parts and parts[0] == root.tag:
        parts = parts[1:]

    if not parts:
        return root

    xpath = "./" + "/".join(parts)
    return root.find(xpath)
