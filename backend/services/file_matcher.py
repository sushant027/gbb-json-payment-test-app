"""Match XML files in initiation/response folders to transactions by batch reference.

Scans a folder of XML files, extracts batch_reference from each,
and matches them to transactions in the database.

Also supports split response mode where success/failure XMLs are
distinguished by the presence/absence of a configurable tag.
"""
import os
import logging
import xml.etree.ElementTree as ET

from backend.services.xml_parser import extract_batch_reference, _strip_all_ns

logger = logging.getLogger(__name__)


def find_matching_files(folder_path, xml_type, mapping_config, batch_references):
    """Scan a folder for XML files and match them to known batch references.

    Args:
        folder_path: Directory to scan for XML files.
        xml_type: 'initiation' or 'response'.
        mapping_config: Full mapping config dict.
        batch_references: Set of batch_reference strings to match against.

    Returns:
        Dict mapping batch_reference -> list of file_paths for matched files.
        Multiple files may match the same batch reference (e.g. response XMLs).
    """
    logger.info("Scanning %s folder: %s (looking for %d batch refs)",
                xml_type, folder_path, len(batch_references))

    if not os.path.isdir(folder_path):
        logger.error("Folder does not exist: %s", folder_path)
        return {}

    matched = {}
    unmatched = []
    errors = []

    xml_files = [f for f in os.listdir(folder_path)
                 if f.lower().endswith(".xml")]

    logger.info("Found %d XML files in %s", len(xml_files), folder_path)

    for filename in xml_files:
        filepath = os.path.join(folder_path, filename)
        logger.debug("Processing file: %s", filepath)

        try:
            batch_ref = extract_batch_reference(filepath, xml_type, mapping_config)

            if batch_ref and batch_ref in batch_references:
                matched.setdefault(batch_ref, []).append(filepath)
                logger.info("MATCHED: %s -> batch_ref=%s", filename, batch_ref)
            elif batch_ref:
                unmatched.append((filename, batch_ref))
                logger.debug("Unmatched file %s with batch_ref=%s", filename, batch_ref)
            else:
                logger.warning("Could not extract batch reference from %s", filename)
                errors.append(filename)
        except Exception:
            logger.exception("Error processing file %s", filename)
            errors.append(filename)

    logger.info("Matching complete: matched=%d batch refs, unmatched=%d, errors=%d",
                len(matched), len(unmatched), len(errors))

    if unmatched:
        logger.debug("Unmatched files: %s",
                     [(f, ref) for f, ref in unmatched[:10]])

    return matched


def classify_response_file(filepath, success_indicator_tag):
    """Classify a response XML file as success or failure based on tag presence.

    Args:
        filepath: Path to the XML file.
        success_indicator_tag: Tag/element name whose presence indicates success.

    Returns:
        'response' if the tag is found (success file),
        'response_fail' if the tag is absent (failure file).
    """
    logger.debug("Classifying response file: %s (looking for tag '%s')",
                 filepath, success_indicator_tag)
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        _strip_all_ns(root)

        # Search for the tag anywhere in the tree
        found = root.find(f".//{success_indicator_tag}")
        if found is not None:
            logger.debug("File %s classified as SUCCESS (tag '%s' found)",
                         os.path.basename(filepath), success_indicator_tag)
            return "response"
        else:
            logger.debug("File %s classified as FAILURE (tag '%s' not found)",
                         os.path.basename(filepath), success_indicator_tag)
            return "response_fail"
    except Exception:
        logger.exception("Error classifying file %s — defaulting to 'response'", filepath)
        return "response"


def find_matching_files_split(folder_path, mapping_config, batch_references):
    """Scan a folder for XML files and match them, classifying as success or failure.

    Used when is_response_xml_split='Y'. Both success and failure response XMLs
    are in the same folder, distinguished by a configurable tag presence.

    Args:
        folder_path: Directory to scan for XML files.
        mapping_config: Full mapping config dict (must have 'response' section).
        batch_references: Set of batch_reference strings to match against.

    Returns:
        Tuple of (success_matches, failure_matches), each being
        dict mapping batch_reference -> list of file_paths.
        Multiple files may match the same batch reference.
    """
    response_config = mapping_config.get("response", {})
    success_indicator_tag = response_config.get("success_indicator_tag", "")

    logger.info("Scanning split response folder: %s (looking for %d batch refs, "
                "success_indicator_tag='%s')",
                folder_path, len(batch_references), success_indicator_tag)

    if not success_indicator_tag:
        logger.error("No success_indicator_tag configured — cannot classify response files")
        return {}, {}

    if not os.path.isdir(folder_path):
        logger.error("Folder does not exist: %s", folder_path)
        return {}, {}

    success_matches = {}
    failure_matches = {}
    unmatched = []
    errors = []

    xml_files = [f for f in os.listdir(folder_path)
                 if f.lower().endswith(".xml")]

    logger.info("Found %d XML files in %s for split response processing",
                len(xml_files), folder_path)

    for filename in xml_files:
        filepath = os.path.join(folder_path, filename)
        logger.debug("Processing split response file: %s", filepath)

        try:
            # Extract batch reference using the 'response' mapping config
            batch_ref = extract_batch_reference(filepath, "response", mapping_config)

            if batch_ref and batch_ref in batch_references:
                # Classify as success or failure
                file_type = classify_response_file(filepath, success_indicator_tag)

                if file_type == "response":
                    success_matches.setdefault(batch_ref, []).append(filepath)
                    logger.info("MATCHED SUCCESS: %s -> batch_ref=%s", filename, batch_ref)
                else:
                    failure_matches.setdefault(batch_ref, []).append(filepath)
                    logger.info("MATCHED FAILURE: %s -> batch_ref=%s", filename, batch_ref)
            elif batch_ref:
                unmatched.append((filename, batch_ref))
                logger.debug("Unmatched split response file %s with batch_ref=%s",
                             filename, batch_ref)
            else:
                logger.warning("Could not extract batch reference from %s", filename)
                errors.append(filename)
        except Exception:
            logger.exception("Error processing split response file %s", filename)
            errors.append(filename)

    logger.info("Split response matching complete: success=%d, failure=%d, "
                "unmatched=%d, errors=%d",
                len(success_matches), len(failure_matches), len(unmatched), len(errors))

    if unmatched:
        logger.debug("Unmatched split response files: %s",
                     [(f, ref) for f, ref in unmatched[:10]])

    return success_matches, failure_matches
