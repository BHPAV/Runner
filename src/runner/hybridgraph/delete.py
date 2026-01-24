#!/usr/bin/env python3
"""
Delete source from hybridgraph with proper ref_count management.

This task:
1. Finds all Structure/Content nodes reachable from the Source
2. Decrements their ref_counts
3. Optionally runs garbage collection for ref_count=0 nodes
4. Deletes the Source node

Usage:
  python delete_source_task.py <source_id> [--gc] [--dry-run]
  Via stack runner: as 'delete_source' task

Parameters:
  source_id: ID of the source to delete
  gc: Run garbage collection after deletion (default: true)
  dry_run: Show what would be deleted without making changes
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Set

try:
    from neo4j import GraphDatabase
except ImportError:
    print("Error: neo4j driver not installed")
    sys.exit(1)


def get_config():
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "password"),
        "database": os.environ.get("TARGET_DB", "hybridgraph"),
    }


def get_source_nodes(driver, database: str, source_id: str) -> Dict:
    """Get all nodes reachable from a source."""
    with driver.session(database=database) as session:
        # Get source and root
        result = session.run("""
            MATCH (src:Source {source_id: $source_id})
            OPTIONAL MATCH (src)-[:HAS_ROOT]->(root:Structure)
            RETURN src.source_id AS source_id, root.merkle AS root_merkle
        """, source_id=source_id)

        record = result.single()
        if not record or not record["source_id"]:
            return {"error": f"Source '{source_id}' not found"}

        root_merkle = record["root_merkle"]
        if not root_merkle:
            return {
                "source_id": source_id,
                "structures": [],
                "contents": [],
            }

        # Get all reachable structures
        # Depth limit (100) prevents runaway queries on deeply nested structures
        result = session.run("""
            MATCH (src:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
            OPTIONAL MATCH (root)-[:CONTAINS*0..100]->(s:Structure)
            WITH collect(DISTINCT s.merkle) + [root.merkle] AS merkles
            UNWIND merkles AS m
            RETURN DISTINCT m AS merkle
        """, source_id=source_id)

        structures = [r["merkle"] for r in result if r["merkle"]]

        # Get all reachable content
        # Depth limit (100) prevents runaway queries on deeply nested structures
        result = session.run("""
            MATCH (src:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
            OPTIONAL MATCH (root)-[:CONTAINS*0..100]->(s:Structure)
            WITH [root] + collect(DISTINCT s) AS all_structs
            UNWIND all_structs AS struct
            MATCH (struct)-[:HAS_VALUE]->(c:Content)
            RETURN DISTINCT c.hash AS hash
        """, source_id=source_id)

        contents = [r["hash"] for r in result if r["hash"]]

        return {
            "source_id": source_id,
            "structures": structures,
            "contents": contents,
        }


def decrement_ref_counts(driver, database: str, structures: List[str], contents: List[str]) -> Dict:
    """Decrement ref_counts for the given nodes."""
    stats = {"structures_updated": 0, "contents_updated": 0}

    with driver.session(database=database) as session:
        # Decrement structure ref_counts
        if structures:
            result = session.run("""
                UNWIND $merkles AS merkle
                MATCH (s:Structure {merkle: merkle})
                SET s.ref_count = CASE
                    WHEN s.ref_count IS NULL THEN 0
                    WHEN s.ref_count <= 1 THEN 0
                    ELSE s.ref_count - 1
                END
                RETURN count(*) AS updated
            """, merkles=structures)
            stats["structures_updated"] = result.single()["updated"]

        # Decrement content ref_counts
        if contents:
            result = session.run("""
                UNWIND $hashes AS hash
                MATCH (c:Content {hash: hash})
                SET c.ref_count = CASE
                    WHEN c.ref_count IS NULL THEN 0
                    WHEN c.ref_count <= 1 THEN 0
                    ELSE c.ref_count - 1
                END
                RETURN count(*) AS updated
            """, hashes=contents)
            stats["contents_updated"] = result.single()["updated"]

    return stats


def delete_source(driver, database: str, source_id: str) -> bool:
    """Delete the Source node and its HAS_ROOT relationship."""
    with driver.session(database=database) as session:
        result = session.run("""
            MATCH (src:Source {source_id: $source_id})
            DETACH DELETE src
            RETURN count(*) AS deleted
        """, source_id=source_id)

        return result.single()["deleted"] > 0


def garbage_collect(driver, database: str) -> Dict:
    """
    Remove nodes with ref_count=0 that have no incoming relationships.

    This is safe because:
    - Content nodes with ref_count=0 and no HAS_VALUE relationships are unused
    - Structure nodes with ref_count=0 and no HAS_ROOT/CONTAINS relationships are unused
    """
    stats = {"structures_deleted": 0, "contents_deleted": 0}

    with driver.session(database=database) as session:
        # Delete orphaned structures with ref_count=0
        result = session.run("""
            MATCH (s:Structure)
            WHERE (s.ref_count IS NULL OR s.ref_count = 0)
              AND NOT ()-[:HAS_ROOT]->(s)
              AND NOT ()-[:CONTAINS]->(s)
            DETACH DELETE s
            RETURN count(*) AS deleted
        """)
        stats["structures_deleted"] = result.single()["deleted"]

        # Delete orphaned content with ref_count=0
        result = session.run("""
            MATCH (c:Content)
            WHERE (c.ref_count IS NULL OR c.ref_count = 0)
              AND NOT ()-[:HAS_VALUE]->(c)
            DELETE c
            RETURN count(*) AS deleted
        """)
        stats["contents_deleted"] = result.single()["deleted"]

    return stats


def delete_source_full(source_id: str, run_gc: bool = True, dry_run: bool = False, verbose: bool = True) -> Dict:
    """
    Full source deletion with ref_count management and optional GC.
    """
    config = get_config()
    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    results = {
        "source_id": source_id,
        "dry_run": dry_run,
        "structures_affected": 0,
        "contents_affected": 0,
        "source_deleted": False,
        "gc_structures": 0,
        "gc_contents": 0,
    }

    try:
        # Get nodes associated with this source
        nodes = get_source_nodes(driver, config["database"], source_id)

        if "error" in nodes:
            results["error"] = nodes["error"]
            return results

        results["structures_affected"] = len(nodes["structures"])
        results["contents_affected"] = len(nodes["contents"])

        if verbose:
            print(f"Source: {source_id}")
            print(f"  Structures to update: {len(nodes['structures'])}")
            print(f"  Content nodes to update: {len(nodes['contents'])}")

        if dry_run:
            if verbose:
                print("  [DRY RUN] No changes made")
            return results

        # Decrement ref_counts
        dec_stats = decrement_ref_counts(
            driver, config["database"],
            nodes["structures"], nodes["contents"]
        )

        if verbose:
            print(f"  Decremented {dec_stats['structures_updated']} structure ref_counts")
            print(f"  Decremented {dec_stats['contents_updated']} content ref_counts")

        # Delete source
        results["source_deleted"] = delete_source(driver, config["database"], source_id)

        if verbose:
            print(f"  Source deleted: {results['source_deleted']}")

        # Run garbage collection
        if run_gc:
            gc_stats = garbage_collect(driver, config["database"])
            results["gc_structures"] = gc_stats["structures_deleted"]
            results["gc_contents"] = gc_stats["contents_deleted"]

            if verbose:
                print(f"  GC: removed {gc_stats['structures_deleted']} structures, {gc_stats['contents_deleted']} content")

    finally:
        driver.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="Delete source from hybridgraph")
    parser.add_argument("source_id", help="Source ID to delete")
    parser.add_argument("--no-gc", action="store_true", help="Skip garbage collection")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    # Check for task params (stack runner mode)
    if os.environ.get("TASK_PARAMS"):
        params = json.loads(os.environ.get("TASK_PARAMS", "{}"))
        source_id = params.get("source_id", args.source_id)
        run_gc = params.get("gc", True)
        dry_run = params.get("dry_run", False)
    else:
        source_id = args.source_id
        run_gc = not args.no_gc
        dry_run = args.dry_run

    print("=" * 60)
    print(f"DELETE SOURCE: {source_id}")
    print("=" * 60)

    results = delete_source_full(
        source_id,
        run_gc=run_gc,
        dry_run=dry_run,
        verbose=not args.quiet
    )

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    if "error" in results:
        print(f"Error: {results['error']}")
    else:
        print(f"Structures affected: {results['structures_affected']}")
        print(f"Content nodes affected: {results['contents_affected']}")
        print(f"Source deleted: {results['source_deleted']}")
        if run_gc:
            print(f"GC structures: {results['gc_structures']}")
            print(f"GC content: {results['gc_contents']}")

    # Output for stack runner
    if os.environ.get("TASK_PARAMS"):
        task_result = {
            "__task_result__": True,
            "output": results,
            "variables": {
                "deleted_source": source_id if results.get("source_deleted") else None,
            },
            "decisions": [
                f"Deleted source: {source_id}" if results.get("source_deleted") else f"Failed to delete: {source_id}",
            ],
        }
        print(json.dumps(task_result))


if __name__ == "__main__":
    main()
