"""
Content-addressable hashing utilities for hybridgraph.

Provides Merkle hash computation for structures and content-addressable
hashes for leaf values. Uses SHA-256 truncated to 128 bits (32 hex chars).
"""

import hashlib
from typing import List

__all__ = ["compute_content_hash", "compute_merkle_hash", "encode_value_for_hash"]


def compute_content_hash(kind: str, key: str, value: str) -> str:
    """
    Compute content-addressable hash for leaf values.

    Uses type-safe encoding to prevent collisions between different types
    with the same string representation (e.g., boolean true vs string "true").

    Hash format: SHA-256 of "{kind}|{key}|{kind}:{value}" truncated to 128 bits.
    The kind prefix in the value portion ensures type safety.

    Args:
        kind: Value type (string, number, boolean, null)
        key: Property name
        value: String representation of the value

    Returns:
        Hash string with 'c:' prefix followed by 32 hex characters
    """
    # Include kind prefix in value to prevent type collisions
    content = f"{kind}|{key}|{kind}:{value}"
    return "c:" + hashlib.sha256(content.encode()).hexdigest()[:32]


def encode_value_for_hash(kind: str, value_str, value_num, value_bool) -> str:
    """
    Encode a value for hashing with type safety.

    Converts Neo4j node value fields to a consistent string representation.

    Args:
        kind: Value type (string, number, boolean, null)
        value_str: String value field from Neo4j node
        value_num: Number value field from Neo4j node
        value_bool: Boolean value field from Neo4j node

    Returns:
        String representation of the value appropriate for the given kind
    """
    if kind == "string":
        return value_str or ""
    elif kind == "number":
        return str(value_num) if value_num is not None else "0"
    elif kind == "boolean":
        return str(value_bool).lower() if value_bool is not None else "false"
    elif kind == "null":
        return "null"
    return str(value_str or "")


def compute_merkle_hash(kind: str, key: str, child_hashes: List[str]) -> str:
    """
    Compute Merkle hash for structure nodes (objects/arrays).

    Args:
        kind: Container type (object, array)
        key: Property name
        child_hashes: List of child node hashes

    Returns:
        Hash string with 'm:' prefix followed by 32 hex characters
    """
    sorted_children = "|".join(sorted(child_hashes))
    content = f"{kind}|{key}|{sorted_children}"
    return "m:" + hashlib.sha256(content.encode()).hexdigest()[:32]
