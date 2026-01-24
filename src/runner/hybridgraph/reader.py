#!/usr/bin/env python3
"""
Read and reconstruct documents from hybridgraph.

Provides functionality to:
- Reconstruct JSON documents from the graph structure
- Search for documents containing specific values
- Compare documents via Merkle hashes

Usage:
  python read_from_hybrid.py get <source_id>
  python read_from_hybrid.py search <key> <value>
  python read_from_hybrid.py diff <source_id1> <source_id2>
  python read_from_hybrid.py list
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

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
        "database": os.environ.get("TARGET_DB", "hybridgraph"),
    }


def get_driver():
    config = get_config()
    return GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))


def list_sources(driver, database: str, limit: int = 100) -> List[Dict]:
    """List all sources in the hybridgraph."""
    with driver.session(database=database) as session:
        result = session.run("""
            MATCH (s:Source)
            OPTIONAL MATCH (s)-[:HAS_ROOT]->(r:Structure)
            RETURN s.source_id AS source_id,
                   s.source_type AS source_type,
                   s.name AS name,
                   s.node_count AS node_count,
                   s.ingested_at AS ingested_at,
                   s.last_synced AS last_synced,
                   r.merkle AS root_merkle
            ORDER BY s.source_id
            LIMIT $limit
        """, limit=limit)

        return [dict(r) for r in result]


def get_document(driver, database: str, source_id: str) -> Optional[Dict[str, Any]]:
    """
    Reconstruct a JSON document from the hybridgraph.

    Traverses from Source -> Structure -> Content nodes and rebuilds
    the original JSON structure.
    """
    with driver.session(database=database) as session:
        # First, check if source exists and get root
        result = session.run("""
            MATCH (source:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
            RETURN root.merkle AS root_merkle, root.kind AS root_kind
        """, source_id=source_id)

        record = result.single()
        if not record:
            return None

        root_merkle = record["root_merkle"]

        # Recursively reconstruct the document
        return _reconstruct_node(session, root_merkle)


def _reconstruct_node(session, merkle: str) -> Any:
    """Recursively reconstruct a node from its merkle hash."""
    # Get the structure node
    result = session.run("""
        MATCH (s:Structure {merkle: $merkle})
        RETURN s.kind AS kind, s.key AS key, s.child_keys AS child_keys
    """, merkle=merkle)

    record = result.single()
    if not record:
        return None

    kind = record["kind"]

    if kind == "object":
        obj = {}

        # Get all child structures
        result = session.run("""
            MATCH (s:Structure {merkle: $merkle})-[r:CONTAINS]->(child:Structure)
            RETURN r.key AS key, child.merkle AS child_merkle
        """, merkle=merkle)

        for r in result:
            obj[r["key"]] = _reconstruct_node(session, r["child_merkle"])

        # Get all child content (leaf values)
        result = session.run("""
            MATCH (s:Structure {merkle: $merkle})-[r:HAS_VALUE]->(c:Content)
            RETURN r.key AS key, c.kind AS kind,
                   c.value_str AS value_str, c.value_num AS value_num,
                   c.value_bool AS value_bool
        """, merkle=merkle)

        for r in result:
            obj[r["key"]] = _extract_value(r["kind"], r["value_str"], r["value_num"], r["value_bool"])

        return obj

    elif kind == "array":
        items = []

        # Get child structures with index
        result = session.run("""
            MATCH (s:Structure {merkle: $merkle})-[r:CONTAINS]->(child:Structure)
            RETURN r.key AS key, r.index AS index, child.merkle AS child_merkle
            ORDER BY CASE WHEN r.index IS NOT NULL THEN r.index ELSE toInteger(r.key) END
        """, merkle=merkle)

        struct_children = [(r["key"], r["child_merkle"]) for r in result]

        # Get child content with index
        result = session.run("""
            MATCH (s:Structure {merkle: $merkle})-[r:HAS_VALUE]->(c:Content)
            RETURN r.key AS key, c.kind AS kind,
                   c.value_str AS value_str, c.value_num AS value_num,
                   c.value_bool AS value_bool
            ORDER BY toInteger(r.key)
        """, merkle=merkle)

        content_children = [(r["key"], r["kind"], r["value_str"], r["value_num"], r["value_bool"]) for r in result]

        # Combine and sort by index
        all_children = []
        for key, child_merkle in struct_children:
            all_children.append((int(key), "struct", child_merkle))
        for key, kind, vs, vn, vb in content_children:
            all_children.append((int(key), "content", (kind, vs, vn, vb)))

        all_children.sort(key=lambda x: x[0])

        for _, child_type, data in all_children:
            if child_type == "struct":
                items.append(_reconstruct_node(session, data))
            else:
                kind, vs, vn, vb = data
                items.append(_extract_value(kind, vs, vn, vb))

        return items

    return None


def _extract_value(kind: str, value_str: Optional[str], value_num: Optional[float], value_bool: Optional[bool]) -> Any:
    """Extract the actual value from Content node fields."""
    if kind == "null":
        return None
    elif kind == "boolean":
        return value_bool
    elif kind == "number":
        return value_num
    elif kind == "string":
        return value_str
    return value_str


def search_by_value(driver, database: str, key: str, value: str, limit: int = 100) -> List[str]:
    """
    Find source IDs that contain a specific key-value pair.

    Returns a list of source_ids that have the given key with the given value.
    """
    with driver.session(database=database) as session:
        result = session.run("""
            MATCH (c:Content {key: $key, value_str: $value})
            MATCH (s:Structure)-[:HAS_VALUE]->(c)
            MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..20]->(s)
            RETURN DISTINCT src.source_id AS source_id
            LIMIT $limit
        """, key=key, value=value, limit=limit)

        return [r["source_id"] for r in result]


def search_by_key(driver, database: str, key: str, limit: int = 100) -> List[Dict]:
    """
    Find all unique values for a specific key across all sources.
    """
    with driver.session(database=database) as session:
        result = session.run("""
            MATCH (c:Content {key: $key})
            RETURN c.value_str AS value_str,
                   c.value_num AS value_num,
                   c.value_bool AS value_bool,
                   c.kind AS kind,
                   c.ref_count AS ref_count
            ORDER BY c.ref_count DESC
            LIMIT $limit
        """, key=key, limit=limit)

        return [dict(r) for r in result]


def diff_documents(driver, database: str, source_id1: str, source_id2: str) -> Dict:
    """
    Compare two documents by their Merkle hashes.

    Returns structures that are:
    - only_in_first: present in source_id1 but not source_id2
    - only_in_second: present in source_id2 but not source_id1
    - shared: present in both
    """
    with driver.session(database=database) as session:
        # Get all structure merkles for each document
        result = session.run("""
            MATCH (s1:Source {source_id: $id1})-[:HAS_ROOT]->(r1:Structure)
            OPTIONAL MATCH (r1)-[:CONTAINS*0..50]->(struct1:Structure)
            WITH collect(DISTINCT COALESCE(struct1.merkle, r1.merkle)) AS merkles1

            MATCH (s2:Source {source_id: $id2})-[:HAS_ROOT]->(r2:Structure)
            OPTIONAL MATCH (r2)-[:CONTAINS*0..50]->(struct2:Structure)
            WITH merkles1, collect(DISTINCT COALESCE(struct2.merkle, r2.merkle)) AS merkles2

            RETURN merkles1, merkles2
        """, id1=source_id1, id2=source_id2)

        record = result.single()
        if not record:
            return {"error": "One or both sources not found"}

        merkles1 = set(record["merkles1"])
        merkles2 = set(record["merkles2"])

        only_in_first = merkles1 - merkles2
        only_in_second = merkles2 - merkles1
        shared = merkles1 & merkles2

        return {
            "source_id1": source_id1,
            "source_id2": source_id2,
            "only_in_first": list(only_in_first),
            "only_in_second": list(only_in_second),
            "shared_count": len(shared),
            "similarity": len(shared) / len(merkles1 | merkles2) if merkles1 | merkles2 else 1.0,
        }


def verify_document(driver, source_db: str, target_db: str, source_id: str) -> Dict:
    """
    Verify that a document in hybridgraph matches the original in jsongraph.

    Reconstructs the document from hybridgraph and compares it to the
    original structure in jsongraph. Reports any discrepancies.
    """
    # Reconstruct from hybridgraph
    hybrid_doc = get_document(driver, target_db, source_id)
    if hybrid_doc is None:
        return {"valid": False, "error": f"Source '{source_id}' not found in hybridgraph"}

    # Reconstruct from jsongraph
    jsongraph_doc = _reconstruct_from_jsongraph(driver, source_db, source_id)
    if jsongraph_doc is None:
        return {"valid": False, "error": f"Document '{source_id}' not found in jsongraph"}

    # Compare
    differences = _deep_compare(jsongraph_doc, hybrid_doc, path="/root")

    return {
        "valid": len(differences) == 0,
        "source_id": source_id,
        "differences": differences,
        "jsongraph_keys": _count_keys(jsongraph_doc) if isinstance(jsongraph_doc, dict) else 0,
        "hybrid_keys": _count_keys(hybrid_doc) if isinstance(hybrid_doc, dict) else 0,
    }


def _reconstruct_from_jsongraph(driver, source_db: str, doc_id: str) -> Optional[Any]:
    """Reconstruct a document from jsongraph (flat Data nodes)."""
    with driver.session(database=source_db) as session:
        # Get root node
        result = session.run("""
            MATCH (d:Data {doc_id: $doc_id, path: '/root'})
            RETURN d.kind AS kind
        """, doc_id=doc_id)

        record = result.single()
        if not record:
            return None

        return _reconstruct_jsongraph_node(session, doc_id, "/root")


def _reconstruct_jsongraph_node(session, doc_id: str, path: str) -> Any:
    """Recursively reconstruct a node from jsongraph."""
    result = session.run("""
        MATCH (d:Data {doc_id: $doc_id, path: $path})
        RETURN d.kind AS kind, d.key AS key,
               d.value_str AS value_str, d.value_num AS value_num,
               d.value_bool AS value_bool
    """, doc_id=doc_id, path=path)

    record = result.single()
    if not record:
        return None

    kind = record["kind"]

    if kind == "object":
        obj = {}
        result = session.run("""
            MATCH (parent:Data {doc_id: $doc_id, path: $path})-[:CONTAINS]->(child:Data)
            RETURN child.path AS child_path, child.key AS key
        """, doc_id=doc_id, path=path)

        for r in result:
            obj[r["key"]] = _reconstruct_jsongraph_node(session, doc_id, r["child_path"])
        return obj

    elif kind == "array":
        items = []
        result = session.run("""
            MATCH (parent:Data {doc_id: $doc_id, path: $path})-[:CONTAINS]->(child:Data)
            RETURN child.path AS child_path, child.key AS key
            ORDER BY toInteger(child.key)
        """, doc_id=doc_id, path=path)

        for r in result:
            items.append(_reconstruct_jsongraph_node(session, doc_id, r["child_path"]))
        return items

    elif kind == "null":
        return None
    elif kind == "boolean":
        return record["value_bool"]
    elif kind == "number":
        return record["value_num"]
    elif kind == "string":
        return record["value_str"]

    return None


def _deep_compare(obj1: Any, obj2: Any, path: str = "") -> List[Dict]:
    """Deep compare two objects and return list of differences."""
    differences = []

    if type(obj1) != type(obj2):
        differences.append({
            "path": path,
            "type": "type_mismatch",
            "expected": type(obj1).__name__,
            "actual": type(obj2).__name__,
        })
        return differences

    if isinstance(obj1, dict):
        keys1 = set(obj1.keys())
        keys2 = set(obj2.keys())

        for key in keys1 - keys2:
            differences.append({
                "path": f"{path}/{key}",
                "type": "missing_in_hybrid",
                "value": obj1[key],
            })

        for key in keys2 - keys1:
            differences.append({
                "path": f"{path}/{key}",
                "type": "extra_in_hybrid",
                "value": obj2[key],
            })

        for key in keys1 & keys2:
            differences.extend(_deep_compare(obj1[key], obj2[key], f"{path}/{key}"))

    elif isinstance(obj1, list):
        if len(obj1) != len(obj2):
            differences.append({
                "path": path,
                "type": "length_mismatch",
                "expected": len(obj1),
                "actual": len(obj2),
            })
        else:
            for i, (item1, item2) in enumerate(zip(obj1, obj2)):
                differences.extend(_deep_compare(item1, item2, f"{path}/{i}"))

    else:
        if obj1 != obj2:
            differences.append({
                "path": path,
                "type": "value_mismatch",
                "expected": obj1,
                "actual": obj2,
            })

    return differences


def _count_keys(obj: Any) -> int:
    """Count total keys/elements in a nested structure."""
    if isinstance(obj, dict):
        return len(obj) + sum(_count_keys(v) for v in obj.values())
    elif isinstance(obj, list):
        return len(obj) + sum(_count_keys(v) for v in obj)
    return 0


def get_source_stats(driver, database: str, source_id: str) -> Dict:
    """Get statistics for a specific source document."""
    with driver.session(database=database) as session:
        result = session.run("""
            MATCH (src:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
            OPTIONAL MATCH (root)-[:CONTAINS*0..50]->(s:Structure)
            WITH src, root, collect(DISTINCT s) + [root] AS structures

            UNWIND structures AS struct
            OPTIONAL MATCH (struct)-[:HAS_VALUE]->(c:Content)
            WITH src, structures, collect(DISTINCT c) AS contents

            RETURN src.source_id AS source_id,
                   src.source_type AS source_type,
                   src.node_count AS original_node_count,
                   size(structures) AS structure_count,
                   size(contents) AS content_count,
                   src.ingested_at AS ingested_at,
                   src.last_synced AS last_synced
        """, source_id=source_id)

        record = result.single()
        if not record:
            return {"error": f"Source {source_id} not found"}

        return dict(record)


def main():
    parser = argparse.ArgumentParser(description="Read documents from hybridgraph")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list command
    list_parser = subparsers.add_parser("list", help="List all sources")
    list_parser.add_argument("--limit", type=int, default=100, help="Max sources to list")

    # get command
    get_parser = subparsers.add_parser("get", help="Reconstruct a document")
    get_parser.add_argument("source_id", help="Source ID to retrieve")
    get_parser.add_argument("--pretty", action="store_true", help="Pretty print JSON")

    # search command
    search_parser = subparsers.add_parser("search", help="Search for documents by key-value")
    search_parser.add_argument("key", help="Key to search for")
    search_parser.add_argument("value", help="Value to match")
    search_parser.add_argument("--limit", type=int, default=100, help="Max results")

    # diff command
    diff_parser = subparsers.add_parser("diff", help="Compare two documents")
    diff_parser.add_argument("source_id1", help="First source ID")
    diff_parser.add_argument("source_id2", help="Second source ID")

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Get source statistics")
    stats_parser.add_argument("source_id", help="Source ID")

    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify document integrity")
    verify_parser.add_argument("source_id", help="Source ID to verify")
    verify_parser.add_argument("--source-db", default="jsongraph", help="Source database (default: jsongraph)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    config = get_config()
    driver = get_driver()

    try:
        if args.command == "list":
            sources = list_sources(driver, config["database"], args.limit)
            print(f"Found {len(sources)} sources:\n")
            for src in sources:
                print(f"  {src['source_id']}")
                print(f"    Type: {src['source_type']}, Nodes: {src['node_count']}")
                if src.get('last_synced'):
                    print(f"    Last synced: {src['last_synced']}")
                print()

        elif args.command == "get":
            doc = get_document(driver, config["database"], args.source_id)
            if doc is None:
                print(f"Error: Source '{args.source_id}' not found")
                sys.exit(1)
            if args.pretty:
                print(json.dumps(doc, indent=2))
            else:
                print(json.dumps(doc))

        elif args.command == "search":
            source_ids = search_by_value(driver, config["database"], args.key, args.value, args.limit)
            print(f"Found {len(source_ids)} sources with {args.key}={args.value}:")
            for sid in source_ids:
                print(f"  {sid}")

        elif args.command == "diff":
            diff_result = diff_documents(driver, config["database"], args.source_id1, args.source_id2)
            print(f"Comparing {args.source_id1} vs {args.source_id2}:")
            print(f"  Similarity: {diff_result['similarity']:.1%}")
            print(f"  Shared structures: {diff_result['shared_count']}")
            print(f"  Only in {args.source_id1}: {len(diff_result['only_in_first'])}")
            print(f"  Only in {args.source_id2}: {len(diff_result['only_in_second'])}")

        elif args.command == "stats":
            stats = get_source_stats(driver, config["database"], args.source_id)
            if "error" in stats:
                print(stats["error"])
                sys.exit(1)
            print(f"Statistics for {args.source_id}:")
            print(f"  Type: {stats['source_type']}")
            print(f"  Original nodes: {stats['original_node_count']}")
            print(f"  Structures: {stats['structure_count']}")
            print(f"  Content nodes: {stats['content_count']}")
            if stats.get('ingested_at'):
                print(f"  Ingested: {stats['ingested_at']}")
            if stats.get('last_synced'):
                print(f"  Last synced: {stats['last_synced']}")

        elif args.command == "verify":
            result = verify_document(driver, args.source_db, config["database"], args.source_id)
            if result["valid"]:
                print(f"Document '{args.source_id}' is valid")
                print(f"  Keys in jsongraph: {result['jsongraph_keys']}")
                print(f"  Keys in hybridgraph: {result['hybrid_keys']}")
            else:
                print(f"Document '{args.source_id}' has issues:")
                if "error" in result:
                    print(f"  Error: {result['error']}")
                else:
                    print(f"  Found {len(result['differences'])} differences:")
                    for diff in result["differences"][:10]:
                        print(f"    {diff['path']}: {diff['type']}")
                        if diff['type'] == 'value_mismatch':
                            print(f"      expected: {diff['expected']}")
                            print(f"      actual: {diff['actual']}")
                sys.exit(1 if not result["valid"] else 0)

    finally:
        driver.close()


if __name__ == "__main__":
    main()
