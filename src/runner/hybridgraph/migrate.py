#!/usr/bin/env python3
"""
Migrate from jsongraph (flat Data nodes) to hybridgraph (Content-Addressable Merkle Graph).

Schema:
  :Source     - Document/API/Database sources (renamed from Document)
  :Structure  - Container nodes (objects/arrays) with Merkle hashes
  :Content    - Leaf value nodes with content-addressable hashes

Usage:
  python migrate_to_hybrid.py [--source-db jsongraph] [--target-db hybridgraph]
"""

import argparse
import os
import sys
from datetime import datetime, timezone

try:
    from neo4j import GraphDatabase
except ImportError:
    print("Error: neo4j driver not installed. Run: pip install neo4j")
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
        "source_db": os.environ.get("NEO4J_DATABASE", "jsongraph"),
        "target_db": "hybridgraph",
    }


def create_database(driver, db_name: str):
    """Create a new database if it doesn't exist."""
    with driver.session(database="system") as session:
        # Check if database exists
        result = session.run("SHOW DATABASES")
        existing = [r["name"] for r in result]

        if db_name not in existing:
            print(f"Creating database: {db_name}")
            session.run(f"CREATE DATABASE {db_name}")
            # Wait for database to be online
            import time
            for _ in range(10):
                result = session.run(f"SHOW DATABASE {db_name}")
                status = result.single()
                if status and status.get("currentStatus") == "online":
                    break
                time.sleep(1)
            print(f"Database {db_name} created and online")
        else:
            print(f"Database {db_name} already exists")


def setup_schema(driver, db_name: str):
    """Create constraints and indexes for the hybrid schema."""
    print("\nSetting up schema...")

    with driver.session(database=db_name) as session:
        # Drop existing constraints/indexes if they exist (for idempotency)
        try:
            session.run("DROP CONSTRAINT source_id_unique IF EXISTS")
            session.run("DROP CONSTRAINT content_hash_unique IF EXISTS")
            session.run("DROP CONSTRAINT structure_merkle_unique IF EXISTS")
        except:
            pass

        # Create constraints
        constraints = [
            ("source_id_unique", "CREATE CONSTRAINT source_id_unique IF NOT EXISTS FOR (s:Source) REQUIRE s.source_id IS UNIQUE"),
            ("content_hash_unique", "CREATE CONSTRAINT content_hash_unique IF NOT EXISTS FOR (c:Content) REQUIRE c.hash IS UNIQUE"),
            ("structure_merkle_unique", "CREATE CONSTRAINT structure_merkle_unique IF NOT EXISTS FOR (s:Structure) REQUIRE s.merkle IS UNIQUE"),
        ]

        for name, query in constraints:
            print(f"  Creating constraint: {name}")
            session.run(query)

        # Create indexes
        indexes = [
            ("content_lookup", "CREATE INDEX content_lookup IF NOT EXISTS FOR (c:Content) ON (c.kind, c.key)"),
            ("content_value_str", "CREATE INDEX content_value_str IF NOT EXISTS FOR (c:Content) ON (c.value_str)"),
            ("content_value_num", "CREATE INDEX content_value_num IF NOT EXISTS FOR (c:Content) ON (c.value_num)"),
            ("structure_kind", "CREATE INDEX structure_kind IF NOT EXISTS FOR (s:Structure) ON (s.kind, s.key)"),
            ("source_type", "CREATE INDEX source_type IF NOT EXISTS FOR (s:Source) ON (s.source_type)"),
        ]

        for name, query in indexes:
            print(f"  Creating index: {name}")
            session.run(query)

    print("Schema setup complete")


def load_source_data(driver, source_db: str) -> dict:
    """Load all data from source database into memory for processing."""
    print(f"\nLoading data from {source_db}...")

    data = {
        "nodes": {},      # path -> node data
        "by_doc": {},     # doc_id -> list of paths
        "children": {},   # parent_path -> list of child_paths
    }

    with driver.session(database=source_db) as session:
        # Load all nodes
        result = session.run("""
            MATCH (d:Data)
            RETURN d.doc_id AS doc_id, d.path AS path, d.kind AS kind, d.key AS key,
                   d.value_str AS value_str, d.value_num AS value_num, d.value_bool AS value_bool
        """)

        for record in result:
            doc_id = record["doc_id"]
            path = record["path"]
            full_path = f"{doc_id}:{path}"

            data["nodes"][full_path] = {
                "doc_id": doc_id,
                "path": path,
                "kind": record["kind"],
                "key": record["key"],
                "value_str": record["value_str"],
                "value_num": record["value_num"],
                "value_bool": record["value_bool"],
            }

            if doc_id not in data["by_doc"]:
                data["by_doc"][doc_id] = []
            data["by_doc"][doc_id].append(path)

        # Load relationships
        result = session.run("""
            MATCH (parent:Data)-[:CONTAINS]->(child:Data)
            RETURN parent.doc_id AS doc_id, parent.path AS parent_path, child.path AS child_path
        """)

        for record in result:
            doc_id = record["doc_id"]
            parent_key = f"{doc_id}:{record['parent_path']}"
            child_key = f"{doc_id}:{record['child_path']}"

            if parent_key not in data["children"]:
                data["children"][parent_key] = []
            data["children"][parent_key].append(child_key)

    print(f"  Loaded {len(data['nodes']):,} nodes from {len(data['by_doc'])} documents")
    return data


def compute_hashes(data: dict) -> dict:
    """Compute content hashes (leaves) and Merkle hashes (containers) bottom-up."""
    print("\nComputing hashes...")

    hashes = {}  # full_path -> hash

    # First pass: hash all leaf nodes
    leaf_count = 0
    for full_path, node in data["nodes"].items():
        if node["kind"] in ["string", "number", "boolean", "null"]:
            value = encode_value_for_hash(
                node["kind"], node["value_str"], node["value_num"], node["value_bool"]
            )
            hashes[full_path] = compute_content_hash(node["kind"], node["key"], value)
            leaf_count += 1

    print(f"  Hashed {leaf_count:,} leaf nodes")

    # Second pass: compute Merkle hashes bottom-up
    # We need to process nodes in order of depth (deepest first)
    def get_depth(path):
        return path.count("/")

    container_paths = [fp for fp, n in data["nodes"].items() if n["kind"] in ["object", "array"]]
    container_paths.sort(key=lambda fp: -get_depth(data["nodes"][fp]["path"]))

    for full_path in container_paths:
        node = data["nodes"][full_path]
        child_paths = data["children"].get(full_path, [])
        child_hashes = [hashes[cp] for cp in child_paths if cp in hashes]

        if child_hashes:
            hashes[full_path] = compute_merkle_hash(node["kind"], node["key"], child_hashes)
        else:
            # Empty container
            hashes[full_path] = compute_merkle_hash(node["kind"], node["key"], [])

    print(f"  Hashed {len(container_paths):,} container nodes")
    return hashes


def migrate_content_layer(driver, target_db: str, data: dict, hashes: dict) -> dict:
    """Create deduplicated Content nodes for all leaf values."""
    print("\nMigrating Content layer...")

    # Collect unique content
    content_map = {}  # hash -> {kind, key, value_str, value_num, value_bool, paths}

    for full_path, node in data["nodes"].items():
        if node["kind"] not in ["string", "number", "boolean", "null"]:
            continue

        h = hashes.get(full_path)
        if not h:
            continue

        if h not in content_map:
            content_map[h] = {
                "hash": h,
                "kind": node["kind"],
                "key": node["key"],
                "value_str": node["value_str"],
                "value_num": node["value_num"],
                "value_bool": node["value_bool"],
                "ref_count": 0,
            }
        content_map[h]["ref_count"] += 1

    print(f"  Found {len(content_map):,} unique content values")

    # Batch insert Content nodes
    with driver.session(database=target_db) as session:
        batch_size = 500
        content_list = list(content_map.values())

        for i in range(0, len(content_list), batch_size):
            batch = content_list[i:i+batch_size]
            session.run("""
                UNWIND $batch AS c
                MERGE (content:Content {hash: c.hash})
                SET content.kind = c.kind,
                    content.key = c.key,
                    content.value_str = c.value_str,
                    content.value_num = c.value_num,
                    content.value_bool = c.value_bool,
                    content.ref_count = c.ref_count
            """, batch=batch)

            if (i + batch_size) % 1000 == 0 or i + batch_size >= len(content_list):
                print(f"    Inserted {min(i + batch_size, len(content_list)):,}/{len(content_list):,} Content nodes")

    return content_map


def migrate_structure_layer(driver, target_db: str, data: dict, hashes: dict) -> dict:
    """Create deduplicated Structure nodes for all containers."""
    print("\nMigrating Structure layer...")

    # Collect unique structures
    structure_map = {}  # merkle -> {kind, key, child_keys, ref_count}

    for full_path, node in data["nodes"].items():
        if node["kind"] not in ["object", "array"]:
            continue

        h = hashes.get(full_path)
        if not h:
            continue

        # Get child keys for objects
        child_keys = []
        if node["kind"] == "object":
            child_paths = data["children"].get(full_path, [])
            child_keys = sorted([data["nodes"][cp]["key"] for cp in child_paths if cp in data["nodes"]])

        if h not in structure_map:
            structure_map[h] = {
                "merkle": h,
                "kind": node["kind"],
                "key": node["key"],
                "child_keys": child_keys,
                "child_count": len(data["children"].get(full_path, [])),
                "ref_count": 0,
            }
        structure_map[h]["ref_count"] += 1

    print(f"  Found {len(structure_map):,} unique structures")

    # Batch insert Structure nodes
    with driver.session(database=target_db) as session:
        batch_size = 500
        structure_list = list(structure_map.values())

        for i in range(0, len(structure_list), batch_size):
            batch = structure_list[i:i+batch_size]
            session.run("""
                UNWIND $batch AS s
                MERGE (structure:Structure {merkle: s.merkle})
                SET structure.kind = s.kind,
                    structure.key = s.key,
                    structure.child_keys = s.child_keys,
                    structure.child_count = s.child_count,
                    structure.ref_count = s.ref_count
            """, batch=batch)

            if (i + batch_size) % 1000 == 0 or i + batch_size >= len(structure_list):
                print(f"    Inserted {min(i + batch_size, len(structure_list)):,}/{len(structure_list):,} Structure nodes")

    return structure_map


def create_structure_relationships(driver, target_db: str, data: dict, hashes: dict):
    """Create CONTAINS relationships between Structure nodes and HAS_VALUE to Content nodes."""
    print("\nCreating Structure relationships...")

    # Collect unique relationships
    contains_rels = set()  # (parent_merkle, child_merkle, key, index)
    has_value_rels = set()  # (structure_merkle, content_hash, key)

    for full_path, node in data["nodes"].items():
        if node["kind"] not in ["object", "array"]:
            continue

        parent_hash = hashes.get(full_path)
        if not parent_hash:
            continue

        child_paths = data["children"].get(full_path, [])
        for idx, child_path in enumerate(child_paths):
            child_node = data["nodes"].get(child_path)
            if not child_node:
                continue

            child_hash = hashes.get(child_path)
            if not child_hash:
                continue

            if child_node["kind"] in ["object", "array"]:
                # Structure -> Structure
                key = child_node["key"]
                index = idx if node["kind"] == "array" else None
                contains_rels.add((parent_hash, child_hash, key, index))
            else:
                # Structure -> Content
                key = child_node["key"]
                has_value_rels.add((parent_hash, child_hash, key))

    print(f"  Found {len(contains_rels):,} CONTAINS relationships")
    print(f"  Found {len(has_value_rels):,} HAS_VALUE relationships")

    # Batch create CONTAINS relationships
    with driver.session(database=target_db) as session:
        batch_size = 500
        contains_list = [{"parent": p, "child": c, "key": k, "index": i} for p, c, k, i in contains_rels]

        for i in range(0, len(contains_list), batch_size):
            batch = contains_list[i:i+batch_size]
            session.run("""
                UNWIND $batch AS rel
                MATCH (parent:Structure {merkle: rel.parent})
                MATCH (child:Structure {merkle: rel.child})
                MERGE (parent)-[r:CONTAINS {key: rel.key}]->(child)
                SET r.index = rel.index
            """, batch=batch)

        print(f"    Created CONTAINS relationships")

        # Batch create HAS_VALUE relationships
        has_value_list = [{"structure": s, "content": c, "key": k} for s, c, k in has_value_rels]

        for i in range(0, len(has_value_list), batch_size):
            batch = has_value_list[i:i+batch_size]
            session.run("""
                UNWIND $batch AS rel
                MATCH (structure:Structure {merkle: rel.structure})
                MATCH (content:Content {hash: rel.content})
                MERGE (structure)-[r:HAS_VALUE {key: rel.key}]->(content)
            """, batch=batch)

        print(f"    Created HAS_VALUE relationships")


def create_source_nodes(driver, target_db: str, data: dict, hashes: dict):
    """Create Source nodes and link to root Structure nodes."""
    print("\nCreating Source nodes...")

    sources = []
    now = datetime.now(timezone.utc).isoformat()

    for doc_id in data["by_doc"].keys():
        # Find root path for this document
        root_path = f"{doc_id}:/root"
        root_merkle = hashes.get(root_path)

        if not root_merkle:
            print(f"  Warning: No root found for {doc_id}")
            continue

        sources.append({
            "source_id": doc_id,
            "source_type": "document",
            "name": doc_id,
            "root_merkle": root_merkle,
            "ingested_at": now,
            "node_count": len(data["by_doc"][doc_id]),
        })

    with driver.session(database=target_db) as session:
        session.run("""
            UNWIND $sources AS s
            MERGE (source:Source {source_id: s.source_id})
            SET source.source_type = s.source_type,
                source.name = s.name,
                source.ingested_at = s.ingested_at,
                source.node_count = s.node_count
            WITH source, s
            MATCH (root:Structure {merkle: s.root_merkle})
            MERGE (source)-[:HAS_ROOT]->(root)
        """, sources=sources)

    print(f"  Created {len(sources)} Source nodes with HAS_ROOT relationships")


def verify_migration(driver, source_db: str, target_db: str):
    """Verify the migration was successful."""
    print("\nVerifying migration...")

    with driver.session(database=source_db) as session:
        result = session.run("MATCH (d:Data) RETURN count(d) AS count")
        source_count = result.single()["count"]

        result = session.run("MATCH (d:Data) RETURN count(DISTINCT d.doc_id) AS count")
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

        result = session.run("MATCH ()-[r:HAS_ROOT]->() RETURN count(r) AS count")
        has_root_count = result.single()["count"]

    total_target = target_sources + target_structures + target_contents
    reduction = (1 - total_target / source_count) * 100 if source_count > 0 else 0

    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"\nSource database ({source_db}):")
    print(f"  Documents: {source_docs:,}")
    print(f"  Total Data nodes: {source_count:,}")
    print(f"\nTarget database ({target_db}):")
    print(f"  Source nodes: {target_sources:,}")
    print(f"  Structure nodes: {target_structures:,}")
    print(f"  Content nodes: {target_contents:,}")
    print(f"  Total nodes: {total_target:,}")
    print(f"\nRelationships:")
    print(f"  HAS_ROOT: {has_root_count:,}")
    print(f"  CONTAINS: {contains_count:,}")
    print(f"  HAS_VALUE: {has_value_count:,}")
    print(f"\nReduction: {reduction:.1f}% ({source_count:,} → {total_target:,} nodes)")
    print("=" * 60)

    return {
        "source_nodes": source_count,
        "source_docs": source_docs,
        "target_sources": target_sources,
        "target_structures": target_structures,
        "target_contents": target_contents,
        "total_target": total_target,
        "reduction_percent": reduction,
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate jsongraph to hybrid schema")
    parser.add_argument("--source-db", default="jsongraph", help="Source database name")
    parser.add_argument("--target-db", default="hybridgraph", help="Target database name")
    args = parser.parse_args()

    config = get_config()
    config["source_db"] = args.source_db
    config["target_db"] = args.target_db

    print("=" * 60)
    print("HYBRID GRAPH MIGRATION")
    print("=" * 60)
    print(f"Source: {config['source_db']}")
    print(f"Target: {config['target_db']}")
    print(f"Neo4j: {config['uri']}")

    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    try:
        # Step 1: Create target database
        create_database(driver, config["target_db"])

        # Step 2: Setup schema
        setup_schema(driver, config["target_db"])

        # Step 3: Load source data
        data = load_source_data(driver, config["source_db"])

        # Step 4: Compute hashes
        hashes = compute_hashes(data)

        # Step 5: Migrate Content layer
        migrate_content_layer(driver, config["target_db"], data, hashes)

        # Step 6: Migrate Structure layer
        migrate_structure_layer(driver, config["target_db"], data, hashes)

        # Step 7: Create relationships
        create_structure_relationships(driver, config["target_db"], data, hashes)

        # Step 8: Create Source nodes
        create_source_nodes(driver, config["target_db"], data, hashes)

        # Step 9: Verify
        results = verify_migration(driver, config["source_db"], config["target_db"])

        print("\nMigration complete!")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
