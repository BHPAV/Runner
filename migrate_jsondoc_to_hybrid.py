#!/usr/bin/env python3
"""
Migrate JsonDoc/JsonNode tree from jsongraph to hybridgraph (Content-Addressable Merkle Graph).

This script migrates the knowledge graph entities (JsonDoc → JsonNode tree) that were
not covered by the original Data node migration.

Source Schema (jsongraph):
  :JsonDoc   - Document metadata (doc_id, doc_type, source)
  :JsonNode  - Tree nodes with path-based keys (kind: object/array/value)
  :ROOT      - JsonDoc → root JsonNode
  :HAS_CHILD - JsonNode → JsonNode (objects)
  :HAS_ITEM  - JsonNode → JsonNode (arrays)

Target Schema (hybridgraph):
  :Source    - Document entry point
  :Structure - Container nodes (objects/arrays) with Merkle hashes
  :Content   - Leaf values with content-addressable hashes
  :HAS_ROOT  - Source → root Structure
  :CONTAINS  - Structure → Structure
  :HAS_VALUE - Structure → Content

Usage:
  python migrate_jsondoc_to_hybrid.py [--batch-size 100] [--dry-run] [--doc-type knowledge_person]
"""

import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

try:
    from neo4j import GraphDatabase
except ImportError:
    print("Error: neo4j driver not installed. Run: pip install neo4j")
    sys.exit(1)


def get_config():
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "password"),
        "source_db": os.environ.get("NEO4J_DATABASE", "jsongraph"),
        "target_db": "hybridgraph",
    }


def compute_content_hash(kind: str, key: str, value: str) -> str:
    """Compute content-addressable hash for leaf values."""
    content = f"{kind}|{key}|{value}"
    return "c:" + hashlib.sha256(content.encode()).hexdigest()[:32]


def compute_merkle_hash(kind: str, key: str, child_hashes: list) -> str:
    """Compute Merkle hash for structure nodes."""
    sorted_children = "|".join(sorted(child_hashes))
    content = f"{kind}|{key}|{sorted_children}"
    return "m:" + hashlib.sha256(content.encode()).hexdigest()[:32]


def extract_key_from_path(path: str) -> str:
    """
    Extract the key from a JSONPath-style path.

    Examples:
        $ → root
        $.user → user
        $.user.name → name
        $.events[0] → 0
        $.events[0].type → type
    """
    if path == "$":
        return "root"

    # Handle array index: $.events[0] → "0"
    if path.endswith("]"):
        match = re.search(r'\[(\d+)\]$', path)
        if match:
            return match.group(1)

    # Handle object key: $.user.name → "name"
    if "." in path:
        # Get last segment, handling array indices in the middle
        last_segment = path.split(".")[-1]
        # Remove any trailing array index
        last_segment = re.sub(r'\[\d+\]$', '', last_segment)
        return last_segment if last_segment else "root"

    return "root"


def map_vtype_to_kind(vtype: str) -> str:
    """Map JsonNode vtype to hybridgraph Content kind."""
    mapping = {
        "string": "string",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
        None: "null",
    }
    return mapping.get(vtype, "string")


def get_document_count(driver, source_db: str, doc_type: str = None) -> int:
    """Get total count of documents to migrate."""
    with driver.session(database=source_db) as session:
        if doc_type:
            result = session.run(
                "MATCH (d:JsonDoc {doc_type: $doc_type}) RETURN count(d) AS count",
                doc_type=doc_type
            )
        else:
            result = session.run("MATCH (d:JsonDoc) RETURN count(d) AS count")
        return result.single()["count"]


def get_document_batch(driver, source_db: str, skip: int, limit: int, doc_type: str = None) -> list:
    """Get a batch of document IDs to process."""
    with driver.session(database=source_db) as session:
        if doc_type:
            result = session.run("""
                MATCH (d:JsonDoc {doc_type: $doc_type})
                RETURN d.doc_id AS doc_id, d.doc_type AS doc_type, d.source AS source
                ORDER BY d.doc_id
                SKIP $skip LIMIT $limit
            """, doc_type=doc_type, skip=skip, limit=limit)
        else:
            result = session.run("""
                MATCH (d:JsonDoc)
                RETURN d.doc_id AS doc_id, d.doc_type AS doc_type, d.source AS source
                ORDER BY d.doc_id
                SKIP $skip LIMIT $limit
            """, skip=skip, limit=limit)
        return [dict(r) for r in result]


def load_document_tree(driver, source_db: str, doc_id: str) -> dict:
    """Load a single document's tree structure from jsongraph."""
    data = {
        "doc_id": doc_id,
        "nodes": {},       # node_id → node data
        "children": {},    # parent_node_id → [(child_node_id, rel_type)]
        "root_node_id": None,
    }

    with driver.session(database=source_db) as session:
        # Get root node
        result = session.run("""
            MATCH (doc:JsonDoc {doc_id: $doc_id})-[:ROOT]->(root:JsonNode)
            RETURN root.node_id AS node_id, root.path AS path, root.kind AS kind,
                   root.keys AS keys, root.value AS value, root.vtype AS vtype
        """, doc_id=doc_id)

        root = result.single()
        if not root:
            return None

        data["root_node_id"] = root["node_id"]
        data["nodes"][root["node_id"]] = {
            "node_id": root["node_id"],
            "path": root["path"],
            "kind": root["kind"],
            "keys": root["keys"],
            "value": root["value"],
            "vtype": root["vtype"],
        }

        # Get all descendant nodes via HAS_CHILD and HAS_ITEM
        result = session.run("""
            MATCH (doc:JsonDoc {doc_id: $doc_id})-[:ROOT]->(root)
            MATCH (root)-[:HAS_CHILD|HAS_ITEM*1..50]->(n)
            RETURN DISTINCT n.node_id AS node_id, n.path AS path, n.kind AS kind,
                   n.keys AS keys, n.value AS value, n.vtype AS vtype
        """, doc_id=doc_id)

        for record in result:
            data["nodes"][record["node_id"]] = {
                "node_id": record["node_id"],
                "path": record["path"],
                "kind": record["kind"],
                "keys": record["keys"],
                "value": record["value"],
                "vtype": record["vtype"],
            }

        # Get all relationships
        result = session.run("""
            MATCH (doc:JsonDoc {doc_id: $doc_id})-[:ROOT]->(root)
            MATCH (parent)-[r:HAS_CHILD|HAS_ITEM]->(child)
            WHERE parent.doc_id = $doc_id AND child.doc_id = $doc_id
            RETURN parent.node_id AS parent_id, child.node_id AS child_id, type(r) AS rel_type
        """, doc_id=doc_id)

        for record in result:
            parent_id = record["parent_id"]
            if parent_id not in data["children"]:
                data["children"][parent_id] = []
            data["children"][parent_id].append((record["child_id"], record["rel_type"]))

    return data


def compute_document_hashes(data: dict) -> dict:
    """Compute hashes for all nodes in a document, bottom-up."""
    hashes = {}  # node_id → hash

    # Build depth map for bottom-up processing
    def get_depth(path):
        if path is None:
            return 0
        return path.count(".") + path.count("[")

    # Sort nodes by depth (deepest first)
    nodes_by_depth = sorted(
        data["nodes"].items(),
        key=lambda x: -get_depth(x[1].get("path", ""))
    )

    for node_id, node in nodes_by_depth:
        key = extract_key_from_path(node.get("path", "$"))
        kind = node.get("kind", "object")

        if kind == "value":
            # Leaf node - compute content hash
            vtype = node.get("vtype", "string")
            value = node.get("value", "")
            if value is None:
                value = "null"
                vtype = "null"
            content_kind = map_vtype_to_kind(vtype)
            hashes[node_id] = compute_content_hash(content_kind, key, str(value))
        else:
            # Container node - compute Merkle hash from children
            child_entries = data["children"].get(node_id, [])
            child_hashes = []
            for child_id, _ in child_entries:
                if child_id in hashes:
                    child_hashes.append(hashes[child_id])
            hashes[node_id] = compute_merkle_hash(kind, key, child_hashes)

    return hashes


def migrate_document(driver, target_db: str, doc_meta: dict, data: dict, hashes: dict, dry_run: bool = False):
    """Migrate a single document to hybridgraph."""
    if data is None or not data["nodes"]:
        return {"status": "skipped", "reason": "no nodes"}

    doc_id = data["doc_id"]
    now = datetime.now(timezone.utc).isoformat()

    # Collect Content nodes (leaf values)
    content_nodes = []
    for node_id, node in data["nodes"].items():
        if node.get("kind") == "value":
            key = extract_key_from_path(node.get("path", "$"))
            vtype = node.get("vtype", "string")
            value = node.get("value", "")

            if value is None:
                value = "null"
                vtype = "null"

            content_kind = map_vtype_to_kind(vtype)
            h = hashes.get(node_id)

            if h:
                content_data = {
                    "hash": h,
                    "kind": content_kind,
                    "key": key,
                    "value_str": None,
                    "value_num": None,
                    "value_bool": None,
                }

                if content_kind == "string":
                    content_data["value_str"] = str(value)
                elif content_kind == "number":
                    try:
                        content_data["value_num"] = float(value) if "." in str(value) else int(value)
                    except (ValueError, TypeError):
                        content_data["value_str"] = str(value)
                elif content_kind == "boolean":
                    content_data["value_bool"] = str(value).lower() == "true"

                content_nodes.append(content_data)

    # Collect Structure nodes (containers)
    structure_nodes = []
    for node_id, node in data["nodes"].items():
        kind = node.get("kind")
        if kind in ["object", "array"]:
            key = extract_key_from_path(node.get("path", "$"))
            h = hashes.get(node_id)

            if h:
                child_entries = data["children"].get(node_id, [])
                child_keys = []
                if kind == "object" and node.get("keys"):
                    child_keys = node["keys"]

                structure_nodes.append({
                    "merkle": h,
                    "kind": kind,
                    "key": key,
                    "child_keys": child_keys,
                    "child_count": len(child_entries),
                })

    # Collect relationships
    contains_rels = []  # Structure → Structure
    has_value_rels = []  # Structure → Content

    for parent_id, children in data["children"].items():
        parent_node = data["nodes"].get(parent_id)
        if not parent_node or parent_node.get("kind") not in ["object", "array"]:
            continue

        parent_hash = hashes.get(parent_id)
        if not parent_hash:
            continue

        for idx, (child_id, rel_type) in enumerate(children):
            child_node = data["nodes"].get(child_id)
            if not child_node:
                continue

            child_hash = hashes.get(child_id)
            if not child_hash:
                continue

            child_key = extract_key_from_path(child_node.get("path", "$"))

            if child_node.get("kind") in ["object", "array"]:
                index = idx if parent_node.get("kind") == "array" else None
                contains_rels.append({
                    "parent": parent_hash,
                    "child": child_hash,
                    "key": child_key,
                    "index": index,
                })
            elif child_node.get("kind") == "value":
                has_value_rels.append({
                    "structure": parent_hash,
                    "content": child_hash,
                    "key": child_key,
                })

    # Source node
    root_merkle = hashes.get(data["root_node_id"])
    source_node = {
        "source_id": f"jsondoc_{doc_id[:16]}",
        "source_type": doc_meta.get("doc_type", "document"),
        "name": doc_meta.get("doc_type", doc_id),
        "original_doc_id": doc_id,
        "ingested_at": now,
        "node_count": len(data["nodes"]),
        "root_merkle": root_merkle,
    }

    if dry_run:
        return {
            "status": "dry_run",
            "content_nodes": len(content_nodes),
            "structure_nodes": len(structure_nodes),
            "contains_rels": len(contains_rels),
            "has_value_rels": len(has_value_rels),
        }

    # Write to hybridgraph
    with driver.session(database=target_db) as session:
        # Create/update Content nodes
        if content_nodes:
            session.run("""
                UNWIND $nodes AS c
                MERGE (content:Content {hash: c.hash})
                ON CREATE SET
                    content.kind = c.kind,
                    content.key = c.key,
                    content.value_str = c.value_str,
                    content.value_num = c.value_num,
                    content.value_bool = c.value_bool,
                    content.ref_count = 1
                ON MATCH SET
                    content.ref_count = content.ref_count + 1
            """, nodes=content_nodes)

        # Create/update Structure nodes
        if structure_nodes:
            session.run("""
                UNWIND $nodes AS s
                MERGE (structure:Structure {merkle: s.merkle})
                ON CREATE SET
                    structure.kind = s.kind,
                    structure.key = s.key,
                    structure.child_keys = s.child_keys,
                    structure.child_count = s.child_count,
                    structure.ref_count = 1
                ON MATCH SET
                    structure.ref_count = structure.ref_count + 1
            """, nodes=structure_nodes)

        # Create CONTAINS relationships
        if contains_rels:
            session.run("""
                UNWIND $rels AS rel
                MATCH (parent:Structure {merkle: rel.parent})
                MATCH (child:Structure {merkle: rel.child})
                MERGE (parent)-[r:CONTAINS {key: rel.key}]->(child)
                SET r.index = rel.index
            """, rels=contains_rels)

        # Create HAS_VALUE relationships
        if has_value_rels:
            session.run("""
                UNWIND $rels AS rel
                MATCH (structure:Structure {merkle: rel.structure})
                MATCH (content:Content {hash: rel.content})
                MERGE (structure)-[:HAS_VALUE {key: rel.key}]->(content)
            """, rels=has_value_rels)

        # Create Source node
        if root_merkle:
            session.run("""
                MERGE (source:Source {source_id: $source.source_id})
                SET source.source_type = $source.source_type,
                    source.name = $source.name,
                    source.original_doc_id = $source.original_doc_id,
                    source.ingested_at = $source.ingested_at,
                    source.node_count = $source.node_count
                WITH source
                MATCH (root:Structure {merkle: $source.root_merkle})
                MERGE (source)-[:HAS_ROOT]->(root)
            """, source=source_node)

    return {
        "status": "migrated",
        "content_nodes": len(content_nodes),
        "structure_nodes": len(structure_nodes),
        "contains_rels": len(contains_rels),
        "has_value_rels": len(has_value_rels),
    }


def verify_migration(driver, source_db: str, target_db: str, doc_type: str = None):
    """Verify migration results."""
    print("\nVerifying migration...")

    with driver.session(database=source_db) as session:
        if doc_type:
            result = session.run(
                "MATCH (d:JsonDoc {doc_type: $doc_type}) RETURN count(d) AS count",
                doc_type=doc_type
            )
        else:
            result = session.run("MATCH (d:JsonDoc) RETURN count(d) AS count")
        source_docs = result.single()["count"]

    with driver.session(database=target_db) as session:
        result = session.run("MATCH (s:Source) RETURN count(s) AS count")
        target_sources = result.single()["count"]

        result = session.run("MATCH (s:Structure) RETURN count(s) AS count")
        target_structures = result.single()["count"]

        result = session.run("MATCH (c:Content) RETURN count(c) AS count")
        target_contents = result.single()["count"]

        result = session.run("MATCH ()-[r:CONTAINS]->() RETURN count(r) AS count")
        contains_count = result.single()["count"]

        result = session.run("MATCH ()-[r:HAS_VALUE]->() RETURN count(r) AS count")
        has_value_count = result.single()["count"]

    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"\nSource ({source_db}):")
    print(f"  JsonDoc documents: {source_docs:,}")
    print(f"\nTarget ({target_db}):")
    print(f"  Source nodes: {target_sources:,}")
    print(f"  Structure nodes: {target_structures:,}")
    print(f"  Content nodes: {target_contents:,}")
    print(f"\nRelationships:")
    print(f"  CONTAINS: {contains_count:,}")
    print(f"  HAS_VALUE: {has_value_count:,}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate JsonDoc/JsonNode tree to hybridgraph"
    )
    parser.add_argument(
        "--batch-size", type=int, default=100,
        help="Number of documents to process per batch"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze without writing to database"
    )
    parser.add_argument(
        "--doc-type", type=str, default=None,
        help="Only migrate specific doc_type (e.g., knowledge_person)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit total documents to migrate"
    )
    parser.add_argument(
        "--skip", type=int, default=0,
        help="Skip first N documents"
    )
    args = parser.parse_args()

    config = get_config()

    print("=" * 60)
    print("JSONDOC TO HYBRIDGRAPH MIGRATION")
    print("=" * 60)
    print(f"Source: {config['source_db']} (JsonDoc/JsonNode)")
    print(f"Target: {config['target_db']}")
    print(f"Batch size: {args.batch_size}")
    print(f"Doc type filter: {args.doc_type or 'all'}")
    if args.dry_run:
        print("Mode: DRY RUN (no writes)")
    print()

    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    try:
        # Get total count
        total_docs = get_document_count(driver, config["source_db"], args.doc_type)
        if args.limit:
            total_docs = min(total_docs, args.limit + args.skip)

        print(f"Documents to process: {total_docs - args.skip:,}")
        print()

        # Process in batches
        processed = 0
        migrated = 0
        skipped = 0
        total_content = 0
        total_structure = 0

        skip = args.skip
        while skip < total_docs:
            limit = args.batch_size
            if args.limit:
                limit = min(limit, args.limit - (skip - args.skip))

            if limit <= 0:
                break

            batch = get_document_batch(
                driver, config["source_db"], skip, limit, args.doc_type
            )

            if not batch:
                break

            for doc_meta in batch:
                doc_id = doc_meta["doc_id"]

                # Load document tree
                data = load_document_tree(driver, config["source_db"], doc_id)

                if data is None:
                    skipped += 1
                    continue

                # Compute hashes
                hashes = compute_document_hashes(data)

                # Migrate
                result = migrate_document(
                    driver, config["target_db"], doc_meta, data, hashes, args.dry_run
                )

                if result["status"] in ["migrated", "dry_run"]:
                    migrated += 1
                    total_content += result["content_nodes"]
                    total_structure += result["structure_nodes"]
                else:
                    skipped += 1

                processed += 1

                if processed % 100 == 0:
                    print(f"  Processed {processed:,}/{total_docs - args.skip:,} "
                          f"(migrated: {migrated:,}, skipped: {skipped:,})")

            skip += len(batch)

        print()
        print(f"Processed: {processed:,}")
        print(f"Migrated: {migrated:,}")
        print(f"Skipped: {skipped:,}")
        print(f"Content nodes created: {total_content:,}")
        print(f"Structure nodes created: {total_structure:,}")

        if not args.dry_run:
            verify_migration(driver, config["source_db"], config["target_db"], args.doc_type)

        print("\nMigration complete!")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
