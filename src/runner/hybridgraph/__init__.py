"""
Hybridgraph - Content-addressable Merkle graph database.

This module provides the deduplicated graph storage layer:
- migrate: Full migration from jsongraph to hybridgraph
- sync: Incremental synchronization
- reader: Document reconstruction and search
- queries: Query API (HybridGraphQuery class)
- health: Health monitoring and integrity checks
- delete: Source deletion with ref_count management
- gc: Garbage collection for orphaned nodes
"""

# Maximum depth for traversing nested JSON structures
# This limits CONTAINS* path traversals to prevent runaway queries
MAX_TRAVERSAL_DEPTH = 100

__all__ = [
    "MAX_TRAVERSAL_DEPTH",
    "migrate",
    "sync",
    "reader",
    "queries",
    "health",
    "delete",
    "gc",
]
