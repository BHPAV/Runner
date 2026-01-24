#!/usr/bin/env python3
"""
Incremental sync from jsongraph to hybridgraph.

This task:
1. Detects new/modified documents in jsongraph (via sync_status tracking)
2. Computes hashes for changed data
3. Merges into hybridgraph (creating new Content/Structure nodes as needed)
4. Updates sync status

Can be run:
- Manually: python sync_to_hybrid_task.py
- Via stack runner: as a scheduled task
- Via APOC: apoc.periodic.repeat()

Environment Variables:
  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
  SOURCE_DB (default: jsongraph)
  TARGET_DB (default: hybridgraph)
"""

import json
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict

try:
    from neo4j import GraphDatabase
except ImportError:
    print("Error: neo4j driver not installed")
    sys.exit(1)

try:
    from runner.utils.hashing import compute_content_hash, compute_merkle_hash, encode_value_for_hash
except ImportError:
    # Fallback for direct execution
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from runner.utils.hashing import compute_content_hash, compute_merkle_hash, encode_value_for_hash


def get_config():
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "password"),
        "source_db": os.environ.get("SOURCE_DB", "jsongraph"),
        "target_db": os.environ.get("TARGET_DB", "hybridgraph"),
    }


def ensure_sync_tracking(driver, source_db: str):
    """Add sync_status index to jsongraph if not exists."""
    with driver.session(database=source_db) as session:
        # Create index for sync tracking
        try:
            session.run("""
                CREATE INDEX data_sync_status IF NOT EXISTS
                FOR (d:Data) ON (d.sync_status)
            """)
        except:
            pass

        # Create index for doc-level tracking
        try:
            session.run("""
                CREATE INDEX data_doc_sync IF NOT EXISTS
                FOR (d:Data) ON (d.doc_id, d.sync_status)
            """)
        except:
            pass


def get_unsynced_documents(driver, source_db: str, limit: int = 10) -> list:
    """Find documents that need syncing."""
    with driver.session(database=source_db) as session:
        # Find doc_ids where any node is unsynced or missing sync_status
        result = session.run("""
            MATCH (d:Data)
            WHERE d.sync_status IS NULL OR d.sync_status = 'pending'
            WITH DISTINCT d.doc_id AS doc_id
            RETURN doc_id
            LIMIT $limit
        """, limit=limit)

        return [r["doc_id"] for r in result]


def load_document_data(driver, source_db: str, doc_id: str) -> dict:
    """Load all data for a specific document."""
    data = {
        "nodes": {},
        "children": {},
    }

    with driver.session(database=source_db) as session:
        # Load nodes
        result = session.run("""
            MATCH (d:Data {doc_id: $doc_id})
            RETURN d.path AS path, d.kind AS kind, d.key AS key,
                   d.value_str AS value_str, d.value_num AS value_num,
                   d.value_bool AS value_bool
        """, doc_id=doc_id)

        for r in result:
            data["nodes"][r["path"]] = {
                "path": r["path"],
                "kind": r["kind"],
                "key": r["key"],
                "value_str": r["value_str"],
                "value_num": r["value_num"],
                "value_bool": r["value_bool"],
            }

        # Load relationships
        result = session.run("""
            MATCH (parent:Data {doc_id: $doc_id})-[:CONTAINS]->(child:Data)
            RETURN parent.path AS parent_path, child.path AS child_path
        """, doc_id=doc_id)

        for r in result:
            if r["parent_path"] not in data["children"]:
                data["children"][r["parent_path"]] = []
            data["children"][r["parent_path"]].append(r["child_path"])

    return data


def compute_document_hashes(data: dict) -> dict:
    """Compute all hashes for a document's nodes."""
    hashes = {}

    # Hash leaves first
    for path, node in data["nodes"].items():
        if node["kind"] in ["string", "number", "boolean", "null"]:
            value = encode_value_for_hash(node["kind"], node["value_str"], node["value_num"], node["value_bool"])
            hashes[path] = compute_content_hash(node["kind"], node["key"], value)

    # Hash containers bottom-up
    def get_depth(path):
        return path.count("/")

    container_paths = [p for p, n in data["nodes"].items() if n["kind"] in ["object", "array"]]
    container_paths.sort(key=lambda p: -get_depth(p))

    for path in container_paths:
        node = data["nodes"][path]
        child_paths = data["children"].get(path, [])
        child_hashes = [hashes[cp] for cp in child_paths if cp in hashes]
        hashes[path] = compute_merkle_hash(node["kind"], node["key"], child_hashes or [])

    return hashes


def get_existing_source_nodes(session, source_id: str) -> dict:
    """Get existing structures and content for a source (for ref_count management on re-sync)."""
    result = session.run("""
        MATCH (src:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
        OPTIONAL MATCH (root)-[:CONTAINS*0..100]->(s:Structure)
        WITH [root] + collect(DISTINCT s) AS all_structs

        UNWIND all_structs AS struct
        OPTIONAL MATCH (struct)-[:HAS_VALUE]->(c:Content)
        WITH collect(DISTINCT struct.merkle) AS structures,
             collect(DISTINCT c.hash) AS contents

        RETURN structures, contents
    """, source_id=source_id)

    record = result.single()
    if record:
        return {
            "structures": [m for m in record["structures"] if m],
            "contents": [h for h in record["contents"] if h],
        }
    return {"structures": [], "contents": []}


def compute_ref_count_changes(old_hashes: set, new_hashes: set) -> tuple:
    """
    Compute which hashes need ref_count changes.

    Returns:
        (to_decrement, to_increment, unchanged)
    """
    to_decrement = old_hashes - new_hashes  # Only in old = decrement
    to_increment = new_hashes - old_hashes  # Only in new = increment
    unchanged = old_hashes & new_hashes     # In both = no change
    return to_decrement, to_increment, unchanged


def decrement_ref_counts(session, structures: list, contents: list):
    """Decrement ref_counts for nodes being removed from this source."""
    if structures:
        session.run("""
            UNWIND $merkles AS merkle
            MATCH (s:Structure {merkle: merkle})
            SET s.ref_count = CASE
                WHEN s.ref_count IS NULL THEN 0
                WHEN s.ref_count <= 1 THEN 0
                ELSE s.ref_count - 1
            END
        """, merkles=structures)

    if contents:
        session.run("""
            UNWIND $hashes AS hash
            MATCH (c:Content {hash: hash})
            SET c.ref_count = CASE
                WHEN c.ref_count IS NULL THEN 0
                WHEN c.ref_count <= 1 THEN 0
                ELSE c.ref_count - 1
            END
        """, hashes=contents)


def sync_document(driver, source_db: str, target_db: str, doc_id: str) -> dict:
    """Sync a single document from source to target."""
    stats = {"content_created": 0, "structure_created": 0, "content_reused": 0, "structure_reused": 0, "is_resync": False}

    # Load document data
    data = load_document_data(driver, source_db, doc_id)
    if not data["nodes"]:
        return {"error": f"No data found for {doc_id}"}

    # Compute hashes for new document first
    hashes = compute_document_hashes(data)

    # Collect new hashes by type
    new_content_hashes = set()
    new_structure_hashes = set()
    for path, node in data["nodes"].items():
        h = hashes.get(path)
        if not h:
            continue
        if node["kind"] in ["string", "number", "boolean", "null"]:
            new_content_hashes.add(h)
        elif node["kind"] in ["object", "array"]:
            new_structure_hashes.add(h)

    with driver.session(database=target_db) as session:
        # Get old hashes from existing source (for re-sync)
        old_nodes = get_existing_source_nodes(session, doc_id)
        old_structure_hashes = set(old_nodes["structures"])
        old_content_hashes = set(old_nodes["contents"])

        # Compute diff-based ref_count changes
        struct_to_decrement, struct_to_increment, struct_unchanged = compute_ref_count_changes(
            old_structure_hashes, new_structure_hashes
        )
        content_to_decrement, content_to_increment, content_unchanged = compute_ref_count_changes(
            old_content_hashes, new_content_hashes
        )

        if old_structure_hashes or old_content_hashes:
            stats["is_resync"] = True

        # Only decrement nodes being removed (not in new set)
        if struct_to_decrement or content_to_decrement:
            decrement_ref_counts(session, list(struct_to_decrement), list(content_to_decrement))

        # 1. Merge Content nodes (leaves)
        # Separate into nodes that need increment vs unchanged
        content_nodes_to_increment = []
        content_nodes_unchanged = []
        for path, node in data["nodes"].items():
            if node["kind"] not in ["string", "number", "boolean", "null"]:
                continue
            h = hashes.get(path)
            if not h:
                continue
            node_data = {
                "hash": h,
                "kind": node["kind"],
                "key": node["key"],
                "value_str": node["value_str"],
                "value_num": node["value_num"],
                "value_bool": node["value_bool"],
            }
            if h in content_unchanged:
                content_nodes_unchanged.append(node_data)
            else:
                content_nodes_to_increment.append(node_data)

        # Merge content nodes that need ref_count increment (new to this source)
        if content_nodes_to_increment:
            result = session.run("""
                UNWIND $nodes AS n
                MERGE (c:Content {hash: n.hash})
                ON CREATE SET c.kind = n.kind, c.key = n.key,
                              c.value_str = n.value_str, c.value_num = n.value_num,
                              c.value_bool = n.value_bool, c.ref_count = 1
                ON MATCH SET c.ref_count = c.ref_count + 1
                RETURN count(*) AS total,
                       sum(CASE WHEN c.ref_count = 1 THEN 1 ELSE 0 END) AS created
            """, nodes=content_nodes_to_increment)
            r = result.single()
            stats["content_created"] = r["created"]
            stats["content_reused"] = r["total"] - r["created"]

        # Merge content nodes that are unchanged (no ref_count change needed)
        if content_nodes_unchanged:
            session.run("""
                UNWIND $nodes AS n
                MERGE (c:Content {hash: n.hash})
                ON CREATE SET c.kind = n.kind, c.key = n.key,
                              c.value_str = n.value_str, c.value_num = n.value_num,
                              c.value_bool = n.value_bool, c.ref_count = 1
            """, nodes=content_nodes_unchanged)
            # These are all reused (they existed in old set)
            stats["content_reused"] += len(content_nodes_unchanged)

        # 2. Merge Structure nodes (containers)
        # Separate into nodes that need increment vs unchanged
        structure_nodes_to_increment = []
        structure_nodes_unchanged = []
        for path, node in data["nodes"].items():
            if node["kind"] not in ["object", "array"]:
                continue
            h = hashes.get(path)
            if not h:
                continue
            child_paths = data["children"].get(path, [])
            child_keys = sorted([data["nodes"][cp]["key"] for cp in child_paths if cp in data["nodes"]])
            node_data = {
                "merkle": h,
                "kind": node["kind"],
                "key": node["key"],
                "child_keys": child_keys,
                "child_count": len(child_paths),
            }
            if h in struct_unchanged:
                structure_nodes_unchanged.append(node_data)
            else:
                structure_nodes_to_increment.append(node_data)

        # Merge structure nodes that need ref_count increment (new to this source)
        if structure_nodes_to_increment:
            result = session.run("""
                UNWIND $nodes AS n
                MERGE (s:Structure {merkle: n.merkle})
                ON CREATE SET s.kind = n.kind, s.key = n.key,
                              s.child_keys = n.child_keys, s.child_count = n.child_count,
                              s.ref_count = 1
                ON MATCH SET s.ref_count = s.ref_count + 1
                RETURN count(*) AS total,
                       sum(CASE WHEN s.ref_count = 1 THEN 1 ELSE 0 END) AS created
            """, nodes=structure_nodes_to_increment)
            r = result.single()
            stats["structure_created"] = r["created"]
            stats["structure_reused"] = r["total"] - r["created"]

        # Merge structure nodes that are unchanged (no ref_count change needed)
        if structure_nodes_unchanged:
            session.run("""
                UNWIND $nodes AS n
                MERGE (s:Structure {merkle: n.merkle})
                ON CREATE SET s.kind = n.kind, s.key = n.key,
                              s.child_keys = n.child_keys, s.child_count = n.child_count,
                              s.ref_count = 1
            """, nodes=structure_nodes_unchanged)
            # These are all reused (they existed in old set)
            stats["structure_reused"] += len(structure_nodes_unchanged)

        # 3. Create relationships (only for newly created structures)
        # CONTAINS relationships
        contains_rels = []
        for path, node in data["nodes"].items():
            if node["kind"] not in ["object", "array"]:
                continue
            parent_hash = hashes.get(path)
            for child_path in data["children"].get(path, []):
                child_node = data["nodes"].get(child_path)
                if not child_node:
                    continue
                child_hash = hashes.get(child_path)
                if child_node["kind"] in ["object", "array"]:
                    contains_rels.append({
                        "parent": parent_hash,
                        "child": child_hash,
                        "key": child_node["key"],
                    })

        if contains_rels:
            session.run("""
                UNWIND $rels AS r
                MATCH (parent:Structure {merkle: r.parent})
                MATCH (child:Structure {merkle: r.child})
                MERGE (parent)-[:CONTAINS {key: r.key}]->(child)
            """, rels=contains_rels)

        # HAS_VALUE relationships
        has_value_rels = []
        for path, node in data["nodes"].items():
            if node["kind"] not in ["object", "array"]:
                continue
            parent_hash = hashes.get(path)
            for child_path in data["children"].get(path, []):
                child_node = data["nodes"].get(child_path)
                if not child_node:
                    continue
                child_hash = hashes.get(child_path)
                if child_node["kind"] in ["string", "number", "boolean", "null"]:
                    has_value_rels.append({
                        "structure": parent_hash,
                        "content": child_hash,
                        "key": child_node["key"],
                    })

        if has_value_rels:
            session.run("""
                UNWIND $rels AS r
                MATCH (s:Structure {merkle: r.structure})
                MATCH (c:Content {hash: r.content})
                MERGE (s)-[:HAS_VALUE {key: r.key}]->(c)
            """, rels=has_value_rels)

        # 4. Create/update Source node
        root_merkle = hashes.get("/root")
        if not root_merkle:
            return {"error": f"Failed to compute root merkle hash for {doc_id}"}

        now = datetime.now(timezone.utc).isoformat()

        session.run("""
            MERGE (source:Source {source_id: $doc_id})
            SET source.source_type = 'document',
                source.name = $doc_id,
                source.node_count = $node_count,
                source.last_synced = $now
            WITH source
            MATCH (root:Structure {merkle: $root_merkle})
            MERGE (source)-[:HAS_ROOT]->(root)
        """, doc_id=doc_id, node_count=len(data["nodes"]), now=now, root_merkle=root_merkle)

    # 5. Mark document as synced in source
    with driver.session(database=source_db) as session:
        session.run("""
            MATCH (d:Data {doc_id: $doc_id})
            SET d.sync_status = 'synced', d.synced_at = $now
        """, doc_id=doc_id, now=datetime.now(timezone.utc).isoformat())

    return stats


def cleanup_orphaned_nodes(driver, target_db: str, verbose: bool = True) -> dict:
    """Remove orphaned Structure and Content nodes with no incoming relationships."""
    stats = {"orphaned_structures": 0, "orphaned_content": 0}

    with driver.session(database=target_db) as session:
        # Find and delete orphaned Structure nodes
        # (no HAS_ROOT or CONTAINS pointing to them)
        result = session.run("""
            MATCH (s:Structure)
            WHERE NOT ()-[:HAS_ROOT]->(s)
              AND NOT ()-[:CONTAINS]->(s)
              AND (s.ref_count IS NULL OR s.ref_count = 0)
            WITH s, s.merkle AS merkle
            DETACH DELETE s
            RETURN count(*) AS deleted
        """)
        stats["orphaned_structures"] = result.single()["deleted"]

        # Find and delete orphaned Content nodes
        # (no HAS_VALUE pointing to them)
        result = session.run("""
            MATCH (c:Content)
            WHERE NOT ()-[:HAS_VALUE]->(c)
              AND (c.ref_count IS NULL OR c.ref_count = 0)
            WITH c, c.hash AS hash
            DELETE c
            RETURN count(*) AS deleted
        """)
        stats["orphaned_content"] = result.single()["deleted"]

        if verbose and (stats["orphaned_structures"] > 0 or stats["orphaned_content"] > 0):
            print(f"  Cleaned up: {stats['orphaned_structures']} orphaned structures, {stats['orphaned_content']} orphaned content")

    return stats


def run_sync(limit: int = 10, verbose: bool = True, cleanup: bool = True) -> dict:
    """Run incremental sync."""
    config = get_config()
    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    results = {
        "documents_synced": 0,
        "content_created": 0,
        "content_reused": 0,
        "structure_created": 0,
        "structure_reused": 0,
        "orphaned_structures_cleaned": 0,
        "orphaned_content_cleaned": 0,
        "errors": [],
    }

    try:
        # Ensure tracking infrastructure
        ensure_sync_tracking(driver, config["source_db"])

        # Get unsynced documents
        unsynced = get_unsynced_documents(driver, config["source_db"], limit)

        if verbose:
            print(f"Found {len(unsynced)} documents to sync")

        for doc_id in unsynced:
            if verbose:
                print(f"  Syncing: {doc_id}...", end=" ")

            try:
                stats = sync_document(driver, config["source_db"], config["target_db"], doc_id)

                if "error" in stats:
                    results["errors"].append(stats["error"])
                    if verbose:
                        print(f"ERROR: {stats['error']}")
                else:
                    results["documents_synced"] += 1
                    results["content_created"] += stats.get("content_created", 0)
                    results["content_reused"] += stats.get("content_reused", 0)
                    results["structure_created"] += stats.get("structure_created", 0)
                    results["structure_reused"] += stats.get("structure_reused", 0)
                    if verbose:
                        print(f"OK (+{stats.get('content_created', 0)} content, +{stats.get('structure_created', 0)} structure)")

            except Exception as e:
                results["errors"].append(f"{doc_id}: {str(e)}")
                if verbose:
                    print(f"ERROR: {e}")

        # Run cleanup for orphaned nodes
        if cleanup:
            cleanup_stats = cleanup_orphaned_nodes(driver, config["target_db"], verbose)
            results["orphaned_structures_cleaned"] = cleanup_stats["orphaned_structures"]
            results["orphaned_content_cleaned"] = cleanup_stats["orphaned_content"]

    finally:
        driver.close()

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync jsongraph to hybridgraph")
    parser.add_argument("--limit", type=int, default=100, help="Max documents to sync per run")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument("--no-cleanup", action="store_true", help="Skip orphaned node cleanup")
    args = parser.parse_args()

    print("=" * 60)
    print("INCREMENTAL SYNC: jsongraph → hybridgraph")
    print("=" * 60)

    results = run_sync(limit=args.limit, verbose=not args.quiet, cleanup=not args.no_cleanup)

    print("\n" + "=" * 60)
    print("SYNC RESULTS")
    print("=" * 60)
    print(f"Documents synced: {results['documents_synced']}")
    print(f"Content nodes: +{results['content_created']} new, {results['content_reused']} reused")
    print(f"Structure nodes: +{results['structure_created']} new, {results['structure_reused']} reused")
    if results.get("orphaned_structures_cleaned", 0) > 0 or results.get("orphaned_content_cleaned", 0) > 0:
        print(f"Orphans cleaned: {results['orphaned_structures_cleaned']} structures, {results['orphaned_content_cleaned']} content")
    if results["errors"]:
        print(f"Errors: {len(results['errors'])}")
        for err in results["errors"][:5]:
            print(f"  - {err}")

    # Output for stack runner
    if os.environ.get("TASK_PARAMS"):
        task_result = {
            "__task_result__": True,
            "output": results,
            "variables": {
                "last_sync_count": results["documents_synced"],
                "sync_complete": len(results["errors"]) == 0,
            },
            "decisions": [
                f"Synced {results['documents_synced']} documents",
                f"Created {results['content_created']} content, {results['structure_created']} structure nodes",
            ],
        }
        print(json.dumps(task_result))


if __name__ == "__main__":
    main()
