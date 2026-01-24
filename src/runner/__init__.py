"""
Runner - Task execution framework with LIFO stack processing and Neo4j integration.

This package provides:
- Stack-based task execution with monadic context accumulation
- Neo4j graph database integration (jsongraph and hybridgraph)
- File format converters (CSV, XML, YAML, Markdown, etc.)
- Content-addressable storage with Merkle hashing
"""

__version__ = "0.1.0"
__author__ = "Runner Project"

from .utils.hashing import compute_content_hash, compute_merkle_hash, encode_value_for_hash
from .utils.neo4j import get_config, get_driver

__all__ = [
    "__version__",
    "compute_content_hash",
    "compute_merkle_hash",
    "encode_value_for_hash",
    "get_config",
    "get_driver",
]
