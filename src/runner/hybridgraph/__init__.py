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

# Import key classes for convenient access
from runner.hybridgraph.queries import HybridGraphQuery
from runner.hybridgraph.sync import run_sync
from runner.hybridgraph.health import run_health_check

__all__ = [
    "MAX_TRAVERSAL_DEPTH",
    "HybridGraphQuery",
    "run_sync",
    "run_health_check",
]
