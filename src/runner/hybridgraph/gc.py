#!/usr/bin/env python3
"""
Garbage collection for hybridgraph.

Removes unreferenced nodes:
- Content nodes with ref_count=0 and no HAS_VALUE relationships
- Structure nodes with ref_count=0 and no HAS_ROOT/CONTAINS relationships

Usage:
  python garbage_collect_task.py [--dry-run] [--verbose]
  Via stack runner: as 'garbage_collect' task
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

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


def analyze_garbage(driver, database: str) -> dict:
    """Analyze nodes that would be garbage collected."""
    with driver.session(database=database) as session:
        # Count orphaned structures
        result = session.run("""
            MATCH (s:Structure)
            WHERE (s.ref_count IS NULL OR s.ref_count = 0)
              AND NOT ()-[:HAS_ROOT]->(s)
              AND NOT ()-[:CONTAINS]->(s)
            RETURN count(s) AS count
        """)
        orphaned_structures = result.single()["count"]

        # Count orphaned content
        result = session.run("""
            MATCH (c:Content)
            WHERE (c.ref_count IS NULL OR c.ref_count = 0)
              AND NOT ()-[:HAS_VALUE]->(c)
            RETURN count(c) AS count
        """)
        orphaned_content = result.single()["count"]

        # Count structures with ref_count=0 but still referenced
        result = session.run("""
            MATCH (s:Structure)
            WHERE (s.ref_count IS NULL OR s.ref_count = 0)
              AND (()-[:HAS_ROOT]->(s) OR ()-[:CONTAINS]->(s))
            RETURN count(s) AS count
        """)
        inconsistent_structures = result.single()["count"]

        # Count content with ref_count=0 but still referenced
        result = session.run("""
            MATCH (c:Content)
            WHERE (c.ref_count IS NULL OR c.ref_count = 0)
              AND ()-[:HAS_VALUE]->(c)
            RETURN count(c) AS count
        """)
        inconsistent_content = result.single()["count"]

        return {
            "orphaned_structures": orphaned_structures,
            "orphaned_content": orphaned_content,
            "inconsistent_structures": inconsistent_structures,
            "inconsistent_content": inconsistent_content,
        }


def fix_ref_counts(driver, database: str) -> dict:
    """Fix ref_count values that don't match actual references."""
    stats = {"structures_fixed": 0, "contents_fixed": 0}

    with driver.session(database=database) as session:
        # Fix Structure ref_counts based on actual incoming relationships
        result = session.run("""
            MATCH (s:Structure)
            OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(s)
            WITH s, count(DISTINCT src) AS actual_refs
            WHERE s.ref_count <> actual_refs
            SET s.ref_count = actual_refs
            RETURN count(*) AS fixed
        """)
        stats["structures_fixed"] = result.single()["fixed"]

        # Note: Content ref_counts are trickier because they depend on
        # structure ref_counts. For now, just ensure they're not negative
        result = session.run("""
            MATCH (c:Content)
            WHERE c.ref_count IS NULL OR c.ref_count < 0
            SET c.ref_count = 0
            RETURN count(*) AS fixed
        """)
        stats["contents_fixed"] = result.single()["fixed"]

    return stats


def garbage_collect(driver, database: str, dry_run: bool = False) -> dict:
    """
    Remove nodes with ref_count=0 that have no incoming relationships.
    """
    stats = {
        "structures_deleted": 0,
        "contents_deleted": 0,
        "dry_run": dry_run,
    }

    with driver.session(database=database) as session:
        if dry_run:
            # Just count what would be deleted
            result = session.run("""
                MATCH (s:Structure)
                WHERE (s.ref_count IS NULL OR s.ref_count = 0)
                  AND NOT ()-[:HAS_ROOT]->(s)
                  AND NOT ()-[:CONTAINS]->(s)
                RETURN count(s) AS count
            """)
            stats["structures_deleted"] = result.single()["count"]

            result = session.run("""
                MATCH (c:Content)
                WHERE (c.ref_count IS NULL OR c.ref_count = 0)
                  AND NOT ()-[:HAS_VALUE]->(c)
                RETURN count(c) AS count
            """)
            stats["contents_deleted"] = result.single()["count"]
        else:
            # Actually delete
            result = session.run("""
                MATCH (s:Structure)
                WHERE (s.ref_count IS NULL OR s.ref_count = 0)
                  AND NOT ()-[:HAS_ROOT]->(s)
                  AND NOT ()-[:CONTAINS]->(s)
                DETACH DELETE s
                RETURN count(*) AS deleted
            """)
            stats["structures_deleted"] = result.single()["deleted"]

            result = session.run("""
                MATCH (c:Content)
                WHERE (c.ref_count IS NULL OR c.ref_count = 0)
                  AND NOT ()-[:HAS_VALUE]->(c)
                DELETE c
                RETURN count(*) AS deleted
            """)
            stats["contents_deleted"] = result.single()["deleted"]

    return stats


def run_gc(dry_run: bool = False, fix_counts: bool = False, verbose: bool = True) -> dict:
    """Run full garbage collection with optional ref_count fixing."""
    config = get_config()
    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "analysis": {},
        "gc": {},
        "fix_counts": {},
    }

    try:
        # Analyze current state
        results["analysis"] = analyze_garbage(driver, config["database"])

        if verbose:
            print("Analysis:")
            print(f"  Orphaned structures: {results['analysis']['orphaned_structures']}")
            print(f"  Orphaned content: {results['analysis']['orphaned_content']}")
            print(f"  Inconsistent structures: {results['analysis']['inconsistent_structures']}")
            print(f"  Inconsistent content: {results['analysis']['inconsistent_content']}")

        # Fix ref_counts if requested
        if fix_counts and not dry_run:
            results["fix_counts"] = fix_ref_counts(driver, config["database"])
            if verbose:
                print(f"\nFixed ref_counts:")
                print(f"  Structures: {results['fix_counts']['structures_fixed']}")
                print(f"  Contents: {results['fix_counts']['contents_fixed']}")

        # Run garbage collection
        results["gc"] = garbage_collect(driver, config["database"], dry_run)

        if verbose:
            action = "Would delete" if dry_run else "Deleted"
            print(f"\nGarbage Collection:")
            print(f"  {action} structures: {results['gc']['structures_deleted']}")
            print(f"  {action} content: {results['gc']['contents_deleted']}")

    finally:
        driver.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="Garbage collection for hybridgraph")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    parser.add_argument("--fix-counts", action="store_true", help="Fix incorrect ref_counts")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    # Check for task params (stack runner mode)
    if os.environ.get("TASK_PARAMS"):
        params = json.loads(os.environ.get("TASK_PARAMS", "{}"))
        dry_run = params.get("dry_run", False)
        fix_counts = params.get("fix_counts", False)
    else:
        dry_run = args.dry_run
        fix_counts = args.fix_counts

    print("=" * 60)
    print("HYBRIDGRAPH GARBAGE COLLECTION")
    print("=" * 60)

    results = run_gc(
        dry_run=dry_run,
        fix_counts=fix_counts,
        verbose=not args.quiet
    )

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    total_deleted = results["gc"]["structures_deleted"] + results["gc"]["contents_deleted"]
    action = "would be deleted" if dry_run else "deleted"
    print(f"Total nodes {action}: {total_deleted}")

    # Output for stack runner
    if os.environ.get("TASK_PARAMS"):
        task_result = {
            "__task_result__": True,
            "output": results,
            "variables": {
                "gc_structures_deleted": results["gc"]["structures_deleted"],
                "gc_contents_deleted": results["gc"]["contents_deleted"],
            },
            "decisions": [
                f"GC: {results['gc']['structures_deleted']} structures, {results['gc']['contents_deleted']} content",
            ],
        }
        print(json.dumps(task_result))


if __name__ == "__main__":
    main()
