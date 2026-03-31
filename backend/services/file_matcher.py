"""Match JSON files in initiation/response folders to transactions by batch reference.

Scans a folder of JSON files, extracts batch_reference from each,
and matches them to transactions in the database.

Also supports split response mode where success/failure JSONs are
distinguished by a configured indicator path/value.
"""
import json
import os
import logging

from backend.services.json_parser import (
    extract_batch_reference, extract_batch_references_multi, check_success_indicator
)

logger = logging.getLogger(__name__)


def find_matching_files(folder_path, json_type, mapping_config, batch_references):
    """Scan a folder for JSON files and match them to known batch references.

    Args:
        folder_path: Directory to scan for JSON files.
        json_type: 'initiation' or 'response'.
        mapping_config: Full mapping config dict.
        batch_references: Set of batch_reference strings to match against.

    Returns:
        Dict mapping batch_reference -> list of file_paths for matched files.
        Multiple files may match the same batch reference (e.g. response JSONs).
    """
    logger.info("Scanning %s folder: %s (looking for %d batch refs)",
                json_type, folder_path, len(batch_references))

    if not os.path.isdir(folder_path):
        logger.error("Folder does not exist: %s", folder_path)
        return {}

    matched = {}
    unmatched = []
    errors = []

    json_files = [f for f in os.listdir(folder_path)
                  if f.lower().endswith(".json")]

    logger.info("Found %d JSON files in %s", len(json_files), folder_path)

    for filename in json_files:
        filepath = os.path.join(folder_path, filename)
        logger.debug("Processing file: %s", filepath)

        try:
            batch_ref = extract_batch_reference(filepath, json_type, mapping_config)

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


def find_matching_files_multi(folder_path, json_type, mapping_config, batch_references):
    """Scan a folder for multi-batch JSON files and match them to batch references.

    Unlike find_matching_files, this extracts ALL batch references from each file
    (a single file may contain multiple BatchDetails entries).

    Args:
        folder_path: Directory to scan for JSON files.
        json_type: 'initiation' or 'response'.
        mapping_config: Full mapping config dict.
        batch_references: Set of batch_reference strings to match against.

    Returns:
        Dict mapping batch_reference -> list of (file_path, batch_index) tuples.
    """
    logger.info("Scanning %s folder (multi-batch): %s (looking for %d batch refs)",
                json_type, folder_path, len(batch_references))

    if not os.path.isdir(folder_path):
        logger.error("Folder does not exist: %s", folder_path)
        return {}

    matched = {}
    json_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".json")]

    logger.info("Found %d JSON files in %s", len(json_files), folder_path)

    for filename in json_files:
        filepath = os.path.join(folder_path, filename)
        try:
            refs = extract_batch_references_multi(filepath, json_type, mapping_config)
            for batch_ref, batch_idx in refs:
                if batch_ref in batch_references:
                    matched.setdefault(batch_ref, []).append((filepath, batch_idx))
                    logger.info("MATCHED (multi): %s batch[%d] -> batch_ref=%s",
                                filename, batch_idx, batch_ref)
        except Exception:
            logger.exception("Error processing multi-batch file %s", filename)

    logger.info("Multi-batch matching complete: matched=%d batch refs", len(matched))
    return matched


def classify_response_file(filepath, mapping_config):
    """Classify a response JSON file as success or failure.

    Uses the success_indicator_path and success_indicator_value from the
    response mapping config to determine classification.

    Args:
        filepath: Path to the JSON file.
        mapping_config: Full mapping config dict.

    Returns:
        'response' if classified as success, 'response_fail' if failure.
    """
    response_config = mapping_config.get("response", {})
    logger.debug("Classifying response file: %s", filepath)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        result = check_success_indicator(data, response_config)
        logger.debug("File %s classified as %s",
                     os.path.basename(filepath),
                     "SUCCESS" if result == "response" else "FAILURE")
        return result
    except Exception:
        logger.exception("Error classifying file %s — defaulting to 'response'", filepath)
        return "response"


def find_matching_files_split(folder_path, mapping_config, batch_references):
    """Scan a folder for JSON files and match them, classifying as success or failure.

    Used when is_response_xml_split='Y'. Both success and failure response JSONs
    are in the same folder, distinguished by a configured indicator.

    Args:
        folder_path: Directory to scan for JSON files.
        mapping_config: Full mapping config dict (must have 'response' section).
        batch_references: Set of batch_reference strings to match against.

    Returns:
        Tuple of (success_matches, failure_matches), each being
        dict mapping batch_reference -> list of file_paths.
    """
    response_config = mapping_config.get("response", {})
    indicator_path = response_config.get("success_indicator_path", "") or response_config.get("success_indicator_tag", "")

    logger.info("Scanning split response folder: %s (looking for %d batch refs, "
                "success_indicator='%s')",
                folder_path, len(batch_references), indicator_path)

    if not indicator_path:
        logger.error("No success indicator configured — cannot classify response files")
        return {}, {}

    if not os.path.isdir(folder_path):
        logger.error("Folder does not exist: %s", folder_path)
        return {}, {}

    success_matches = {}
    failure_matches = {}
    unmatched = []
    errors = []

    json_files = [f for f in os.listdir(folder_path)
                  if f.lower().endswith(".json")]

    logger.info("Found %d JSON files in %s for split response processing",
                len(json_files), folder_path)

    for filename in json_files:
        filepath = os.path.join(folder_path, filename)
        logger.debug("Processing split response file: %s", filepath)

        try:
            batch_ref = extract_batch_reference(filepath, "response", mapping_config)

            if batch_ref and batch_ref in batch_references:
                file_type = classify_response_file(filepath, mapping_config)

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

    return success_matches, failure_matches
