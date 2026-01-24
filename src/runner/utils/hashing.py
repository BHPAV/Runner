"""
Content-addressable hashing utilities for hybridgraph.

Provides Merkle hash computation for structures and content-addressable
hashes for leaf values. Uses SHA-256 truncated to 128 bits (32 hex chars).
"""

import hashlib
from typing import List

__all__ = ["compute_content_hash", "compute_merkle_hash"]


def compute_content_hash(kind: str, key: str, value: str) -> str:
    """
    Compute content-addressable hash for leaf values.

    Args:
        kind: Value type (string, number, boolean, null)
        key: Property name
        value: String representation of the value

    Returns:
        Hash string with 'c:' prefix followed by 32 hex characters
    """
    content = f"{kind}|{key}|{value}"
    return "c:" + hashlib.sha256(content.encode()).hexdigest()[:32]


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
