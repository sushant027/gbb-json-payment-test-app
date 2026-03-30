"""Auto-generate field values based on configuration rules.

Supports: numeric, alphanumeric, with configurable prefix and length.
"""
import random
import string
import logging

logger = logging.getLogger(__name__)


def generate_value(config):
    """Generate a value based on auto_generate configuration.

    Args:
        config: dict with keys:
            - type: 'numeric' or 'alphanumeric'
            - prefix: string prefix (e.g. 'BATCH', 'CREDIT')
            - length: total length of generated part (excluding prefix)

    Returns:
        Generated string value.
    """
    gen_type = config.get("type", "alphanumeric")
    prefix = config.get("prefix", "")
    length = config.get("length", 10)

    remaining = max(length - len(prefix), 4)

    if gen_type == "numeric":
        chars = "".join(random.choices(string.digits, k=remaining))
    else:
        chars = "".join(random.choices(string.ascii_uppercase + string.digits, k=remaining))

    value = prefix + chars
    logger.debug("Auto-generated value: type=%s, prefix=%s, length=%d -> %s",
                 gen_type, prefix, length, value)
    return value
