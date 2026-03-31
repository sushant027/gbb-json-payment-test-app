"""Scheme management routes — CRUD + JSON parsing for mapping UI."""
import json
import logging
from flask import Blueprint, request, jsonify
from backend import models
from backend.services.json_parser import parse_json_to_tree

logger = logging.getLogger(__name__)
schemes_bp = Blueprint("schemes", __name__)


@schemes_bp.route("", methods=["GET"])
def list_schemes():
    """List all schemes."""
    logger.info("GET /api/schemes — listing all schemes")
    schemes = models.get_all_schemes()
    logger.debug("Found %d schemes", len(schemes))
    return jsonify(schemes)


@schemes_bp.route("", methods=["POST"])
def create_scheme():
    """Create a new scheme."""
    data = request.get_json()
    if not data or not data.get("scheme_name"):
        logger.warning("Create scheme: missing scheme_name")
        return jsonify({"error": "scheme_name is required"}), 400

    scheme_name = data["scheme_name"].strip()
    is_response_xml_split = data.get("is_response_xml_split", "N").upper()
    if is_response_xml_split not in ("Y", "N"):
        is_response_xml_split = "N"
    logger.info("POST /api/schemes — creating scheme: %s (is_response_xml_split=%s)",
                scheme_name, is_response_xml_split)

    try:
        scheme_id = models.create_scheme(scheme_name, is_response_xml_split=is_response_xml_split)
        return jsonify({"id": scheme_id, "scheme_name": scheme_name,
                        "is_response_xml_split": is_response_xml_split}), 201
    except Exception as e:
        logger.exception("Failed to create scheme")
        return jsonify({"error": str(e)}), 500


@schemes_bp.route("/<int:scheme_id>", methods=["GET"])
def get_scheme(scheme_id):
    """Get a scheme with its mapping config."""
    logger.info("GET /api/schemes/%d", scheme_id)
    scheme = models.get_scheme(scheme_id)
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    # Parse mapping_config from JSON string to dict
    if scheme.get("mapping_config"):
        try:
            scheme["mapping_config"] = json.loads(scheme["mapping_config"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid mapping_config JSON for scheme %d", scheme_id)
            scheme["mapping_config"] = None

    # Parse filename_pattern from JSON string to dict
    if scheme.get("filename_pattern"):
        try:
            scheme["filename_pattern"] = json.loads(scheme["filename_pattern"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid filename_pattern JSON for scheme %d", scheme_id)
            scheme["filename_pattern"] = None

    return jsonify(scheme)


@schemes_bp.route("/<int:scheme_id>", methods=["DELETE"])
def delete_scheme(scheme_id):
    """Delete a scheme."""
    logger.info("DELETE /api/schemes/%d", scheme_id)
    scheme = models.get_scheme(scheme_id)
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    models.delete_scheme(scheme_id)
    return jsonify({"message": "Scheme deleted"})


@schemes_bp.route("/<int:scheme_id>/mapping", methods=["PUT"])
def update_mapping(scheme_id):
    """Save/update the mapping config for a scheme."""
    logger.info("PUT /api/schemes/%d/mapping", scheme_id)

    scheme = models.get_scheme(scheme_id)
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Mapping config data is required"}), 400

    logger.debug("Saving mapping config for scheme %d: keys=%s",
                 scheme_id, list(data.keys()))

    # Extract and save filename_pattern separately (stored in its own column)
    filename_pattern = data.pop("filename_pattern", None)
    models.update_scheme_filename_pattern(scheme_id, filename_pattern)

    models.update_scheme_mapping(scheme_id, data)
    return jsonify({"message": "Mapping config updated", "scheme_id": scheme_id})


@schemes_bp.route("/<int:scheme_id>/parse-json", methods=["POST"])
def parse_json(scheme_id):
    """Upload a sample JSON and return its parsed tree structure for the mapping UI.

    Also stores the JSON as a template on the scheme.
    """
    logger.info("POST /api/schemes/%d/parse-json", scheme_id)

    scheme = models.get_scheme(scheme_id)
    if not scheme:
        return jsonify({"error": "Scheme not found"}), 404

    json_type = request.form.get("json_type")
    if json_type not in ("request", "initiation", "response", "response_fail"):
        logger.warning("Invalid json_type: %s", json_type)
        return jsonify({"error": "json_type must be 'request', 'initiation', 'response', or 'response_fail'"}), 400
    logger.debug("Parsing JSON for type: %s", json_type)

    # Accept either file upload or raw JSON in body
    json_content = None
    if "file" in request.files:
        file = request.files["file"]
        json_content = file.read().decode("utf-8")
        logger.debug("Received JSON file upload: %s (%d bytes)", file.filename, len(json_content))
    elif request.form.get("json_content"):
        json_content = request.form["json_content"]
        logger.debug("Received raw JSON content (%d bytes)", len(json_content))

    if not json_content:
        return jsonify({"error": "No JSON content provided. Upload a file or provide json_content."}), 400

    try:
        # Parse JSON to tree structure
        tree = parse_json_to_tree(json_content)

        # Store as template (reuses the *_xml_template columns for JSON content)
        models.update_scheme_xml_template(scheme_id, json_type, json_content)
        logger.info("Stored %s JSON template for scheme %d", json_type, scheme_id)

        return jsonify({
            "json_type": json_type,
            "tree": tree,
            "message": f"Sample {json_type} JSON parsed successfully"
        })
    except Exception as e:
        logger.exception("Failed to parse JSON")
        return jsonify({"error": f"Failed to parse JSON: {str(e)}"}), 400
