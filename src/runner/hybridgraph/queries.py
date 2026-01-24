#!/usr/bin/env python3
"""
Query API for hybridgraph.

Provides a clean Python API for querying the hybridgraph database:
- Document retrieval and reconstruction
- Content and structure search
- Document comparison (diff)
- Statistics and analysis

This module can be imported into other scripts or used directly.

Usage:
  from hybridgraph_queries import HybridGraphQuery

  query = HybridGraphQuery()
  doc = query.get_document("my_source_id")
  sources = query.search_content("status", "done")
  stats = query.get_stats()
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    from neo4j import GraphDatabase
except ImportError:
    print("Error: neo4j driver not installed. Run: pip install neo4j")
    sys.exit(1)


class HybridGraphQuery:
    """Query interface for hybridgraph database."""

    def __init__(self, uri: str = None, user: str = None, password: str = None, database: str = None):
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.password = password or os.environ.get("NEO4J_PASSWORD", "password")
        self.database = database or os.environ.get("TARGET_DB", "hybridgraph")
        self._driver = None

    @property
    def driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        return self._driver

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # =========================================================================
    # Document Operations
    # =========================================================================

    def get_document(self, source_id: str, use_batch: bool = True) -> Optional[Dict[str, Any]]:
        """
        Reconstruct a JSON document from the hybridgraph.

        Args:
            source_id: The source identifier
            use_batch: If True, use optimized batch query (default).
                       If False, use recursive queries.

        Returns:
            The reconstructed JSON document, or None if not found
        """
        if use_batch:
            return self.get_document_batch(source_id)

        # Original recursive implementation
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (source:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
                RETURN root.merkle AS root_merkle, root.kind AS root_kind
            """, source_id=source_id)

            record = result.single()
            if not record:
                return None

            return self._reconstruct_node(session, record["root_merkle"])

    def get_document_batch(self, source_id: str) -> Optional[Dict[str, Any]]:
        """
        Reconstruct a JSON document using batch query (optimized).

        Uses a single query to fetch the entire subgraph, then reconstructs
        in memory. Much faster than recursive queries for large documents.
        """
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (source:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)

                // Get all structures in the tree
                OPTIONAL MATCH path = (root)-[:CONTAINS*0..100]->(s:Structure)
                WITH root, collect(DISTINCT s) + [root] AS structures

                // Get all content values
                UNWIND structures AS struct
                OPTIONAL MATCH (struct)-[hv:HAS_VALUE]->(c:Content)
                WITH root, structures,
                     collect({
                         parent_merkle: struct.merkle,
                         key: hv.key,
                         hash: c.hash,
                         kind: c.kind,
                         value_str: c.value_str,
                         value_num: c.value_num,
                         value_bool: c.value_bool
                     }) AS contents

                // Get all contains relationships
                UNWIND structures AS struct
                OPTIONAL MATCH (struct)-[rel:CONTAINS]->(child:Structure)
                WITH root, structures, contents,
                     collect({
                         parent_merkle: struct.merkle,
                         child_merkle: child.merkle,
                         key: rel.key
                     }) AS contains_rels

                // Return structure info
                RETURN root.merkle AS root_merkle,
                       [s IN structures | {
                           merkle: s.merkle,
                           kind: s.kind,
                           key: s.key,
                           child_keys: s.child_keys
                       }] AS structures,
                       contents,
                       contains_rels
            """, source_id=source_id)

            record = result.single()
            if not record:
                return None

            return self._build_tree_from_batch(
                record["root_merkle"],
                record["structures"],
                record["contents"],
                record["contains_rels"]
            )

    def _build_tree_from_batch(self, root_merkle: str, structures: list,
                                contents: list, contains_rels: list) -> Any:
        """Build document tree from batch query results."""
        # Index structures by merkle
        struct_map = {s["merkle"]: s for s in structures if s["merkle"]}

        # Index contents by parent merkle
        content_map = {}  # merkle -> list of content items
        for c in contents:
            if c["parent_merkle"] and c["hash"]:
                if c["parent_merkle"] not in content_map:
                    content_map[c["parent_merkle"]] = []
                content_map[c["parent_merkle"]].append(c)

        # Index contains relationships
        children_map = {}  # parent_merkle -> list of (key, child_merkle)
        for rel in contains_rels:
            if rel["parent_merkle"] and rel["child_merkle"]:
                if rel["parent_merkle"] not in children_map:
                    children_map[rel["parent_merkle"]] = []
                children_map[rel["parent_merkle"]].append((rel["key"], rel["child_merkle"]))

        def build_node(merkle: str) -> Any:
            struct = struct_map.get(merkle)
            if not struct:
                return None

            kind = struct["kind"]

            if kind == "object":
                obj = {}
                # Add child structures
                for key, child_merkle in children_map.get(merkle, []):
                    obj[key] = build_node(child_merkle)
                # Add content values
                for c in content_map.get(merkle, []):
                    obj[c["key"]] = self._extract_value(c["kind"], c["value_str"], c["value_num"], c["value_bool"])
                return obj

            elif kind == "array":
                items = []
                # Collect all children with their indices
                all_items = []
                for key, child_merkle in children_map.get(merkle, []):
                    all_items.append((int(key), build_node(child_merkle)))
                for c in content_map.get(merkle, []):
                    all_items.append((int(c["key"]), self._extract_value(c["kind"], c["value_str"], c["value_num"], c["value_bool"])))
                # Sort by index and extract values
                all_items.sort(key=lambda x: x[0])
                return [item[1] for item in all_items]

            return None

        return build_node(root_merkle)

    def _reconstruct_node(self, session, merkle: str) -> Any:
        """Recursively reconstruct a node from its merkle hash."""
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

            # Get child structures
            result = session.run("""
                MATCH (s:Structure {merkle: $merkle})-[r:CONTAINS]->(child:Structure)
                RETURN r.key AS key, child.merkle AS child_merkle
            """, merkle=merkle)

            for r in result:
                obj[r["key"]] = self._reconstruct_node(session, r["child_merkle"])

            # Get child content
            result = session.run("""
                MATCH (s:Structure {merkle: $merkle})-[r:HAS_VALUE]->(c:Content)
                RETURN r.key AS key, c.kind AS kind,
                       c.value_str AS value_str, c.value_num AS value_num,
                       c.value_bool AS value_bool
            """, merkle=merkle)

            for r in result:
                obj[r["key"]] = self._extract_value(r["kind"], r["value_str"], r["value_num"], r["value_bool"])

            return obj

        elif kind == "array":
            items = []

            # Get child structures
            result = session.run("""
                MATCH (s:Structure {merkle: $merkle})-[r:CONTAINS]->(child:Structure)
                RETURN r.key AS key, child.merkle AS child_merkle
                ORDER BY CASE WHEN r.index IS NOT NULL THEN r.index ELSE toInteger(r.key) END
            """, merkle=merkle)

            struct_children = [(int(r["key"]), "struct", r["child_merkle"]) for r in result]

            # Get child content
            result = session.run("""
                MATCH (s:Structure {merkle: $merkle})-[r:HAS_VALUE]->(c:Content)
                RETURN r.key AS key, c.kind AS kind,
                       c.value_str AS value_str, c.value_num AS value_num,
                       c.value_bool AS value_bool
                ORDER BY toInteger(r.key)
            """, merkle=merkle)

            content_children = [(int(r["key"]), "content", (r["kind"], r["value_str"], r["value_num"], r["value_bool"])) for r in result]

            # Combine and sort
            all_children = struct_children + content_children
            all_children.sort(key=lambda x: x[0])

            for _, child_type, data in all_children:
                if child_type == "struct":
                    items.append(self._reconstruct_node(session, data))
                else:
                    kind, vs, vn, vb = data
                    items.append(self._extract_value(kind, vs, vn, vb))

            return items

        return None

    def _extract_value(self, kind: str, value_str, value_num, value_bool) -> Any:
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

    def list_sources(self, limit: int = 100) -> List[Dict]:
        """List all source documents."""
        with self.driver.session(database=self.database) as session:
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

    # =========================================================================
    # Search Operations
    # =========================================================================

    def search_content(self, key: str, value: str, limit: int = 100) -> List[str]:
        """
        Find source IDs that contain a specific key-value pair.

        Args:
            key: The key to search for
            value: The string value to match
            limit: Maximum results to return

        Returns:
            List of source_ids containing the key-value pair
        """
        with self.driver.session(database=self.database) as session:
            # Depth limit (100) prevents runaway queries on deeply nested structures
            result = session.run("""
                MATCH (c:Content {key: $key, value_str: $value})
                MATCH (s:Structure)-[:HAS_VALUE]->(c)
                MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..100]->(s)
                RETURN DISTINCT src.source_id AS source_id
                LIMIT $limit
            """, key=key, value=value, limit=limit)

            return [r["source_id"] for r in result]

    def search_by_key(self, key: str, limit: int = 100) -> List[Dict]:
        """Find all unique values for a specific key."""
        with self.driver.session(database=self.database) as session:
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

    def find_shared_structures(self, min_refs: int = 10, limit: int = 50) -> List[Dict]:
        """
        Find structures that are shared across multiple sources.

        Args:
            min_refs: Minimum reference count to include
            limit: Maximum results

        Returns:
            List of shared structures with their ref_counts
        """
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (s:Structure)
                WHERE s.ref_count >= $min_refs
                RETURN s.merkle AS merkle,
                       s.kind AS kind,
                       s.key AS key,
                       s.child_keys AS child_keys,
                       s.child_count AS child_count,
                       s.ref_count AS ref_count
                ORDER BY s.ref_count DESC
                LIMIT $limit
            """, min_refs=min_refs, limit=limit)

            return [dict(r) for r in result]

    def find_shared_content(self, min_refs: int = 10, limit: int = 50) -> List[Dict]:
        """
        Find content values that are shared across multiple structures.

        Args:
            min_refs: Minimum reference count to include
            limit: Maximum results

        Returns:
            List of shared content with their ref_counts
        """
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (c:Content)
                WHERE c.ref_count >= $min_refs
                RETURN c.hash AS hash,
                       c.kind AS kind,
                       c.key AS key,
                       c.value_str AS value_str,
                       c.value_num AS value_num,
                       c.ref_count AS ref_count
                ORDER BY c.ref_count DESC
                LIMIT $limit
            """, min_refs=min_refs, limit=limit)

            return [dict(r) for r in result]

    # =========================================================================
    # Comparison Operations
    # =========================================================================

    def diff_sources(self, source_id1: str, source_id2: str) -> Dict:
        """
        Compare two documents by their Merkle hashes.

        Returns:
            Dictionary with:
            - only_in_first: merkles only in source_id1
            - only_in_second: merkles only in source_id2
            - shared_count: count of shared structures
            - similarity: Jaccard similarity (0-1)
        """
        with self.driver.session(database=self.database) as session:
            # Depth limit (100) prevents runaway queries on deeply nested structures
            result = session.run("""
                MATCH (s1:Source {source_id: $id1})-[:HAS_ROOT]->(r1:Structure)
                OPTIONAL MATCH (r1)-[:CONTAINS*0..100]->(struct1:Structure)
                WITH collect(DISTINCT COALESCE(struct1.merkle, r1.merkle)) AS merkles1

                MATCH (s2:Source {source_id: $id2})-[:HAS_ROOT]->(r2:Structure)
                OPTIONAL MATCH (r2)-[:CONTAINS*0..100]->(struct2:Structure)
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
            union = merkles1 | merkles2

            return {
                "source_id1": source_id1,
                "source_id2": source_id2,
                "only_in_first": list(only_in_first),
                "only_in_second": list(only_in_second),
                "shared_count": len(shared),
                "similarity": len(shared) / len(union) if union else 1.0,
            }

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_source_stats(self, source_id: str) -> Dict:
        """Get statistics for a specific source document."""
        with self.driver.session(database=self.database) as session:
            # Depth limit (100) prevents runaway queries on deeply nested structures
            result = session.run("""
                MATCH (src:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
                OPTIONAL MATCH (root)-[:CONTAINS*0..100]->(s:Structure)
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

    def get_stats(self) -> Dict:
        """Get overall database statistics."""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (src:Source)
                WITH count(src) AS source_count

                MATCH (s:Structure)
                WITH source_count, count(s) AS structure_count

                MATCH (c:Content)
                WITH source_count, structure_count, count(c) AS content_count

                MATCH ()-[r:HAS_ROOT]->()
                WITH source_count, structure_count, content_count, count(r) AS has_root_count

                MATCH ()-[r:CONTAINS]->()
                WITH source_count, structure_count, content_count, has_root_count, count(r) AS contains_count

                MATCH ()-[r:HAS_VALUE]->()
                RETURN source_count, structure_count, content_count,
                       has_root_count, contains_count, count(r) AS has_value_count
            """)

            record = result.single()
            return {
                "sources": record["source_count"],
                "structures": record["structure_count"],
                "contents": record["content_count"],
                "relationships": {
                    "HAS_ROOT": record["has_root_count"],
                    "CONTAINS": record["contains_count"],
                    "HAS_VALUE": record["has_value_count"],
                },
                "total_nodes": record["source_count"] + record["structure_count"] + record["content_count"],
                "total_relationships": record["has_root_count"] + record["contains_count"] + record["has_value_count"],
            }

    def get_deduplication_stats(self) -> Dict:
        """Get statistics about deduplication effectiveness."""
        with self.driver.session(database=self.database) as session:
            # Most reused content
            result = session.run("""
                MATCH (c:Content)
                WHERE c.ref_count > 1
                RETURN c.key AS key, c.value_str AS value, c.ref_count AS ref_count
                ORDER BY c.ref_count DESC
                LIMIT 10
            """)
            top_content = [dict(r) for r in result]

            # Most reused structures
            result = session.run("""
                MATCH (s:Structure)
                WHERE s.ref_count > 1
                RETURN s.kind AS kind, s.key AS key, s.child_count AS child_count, s.ref_count AS ref_count
                ORDER BY s.ref_count DESC
                LIMIT 10
            """)
            top_structures = [dict(r) for r in result]

            # Calculate deduplication ratio
            result = session.run("""
                MATCH (c:Content)
                WITH sum(c.ref_count) AS total_refs, count(c) AS unique_count
                RETURN total_refs, unique_count,
                       CASE WHEN total_refs > 0 THEN (toFloat(total_refs - unique_count) / total_refs) * 100 ELSE 0 END AS dedup_percent
            """)
            content_stats = result.single()

            result = session.run("""
                MATCH (s:Structure)
                WITH sum(s.ref_count) AS total_refs, count(s) AS unique_count
                RETURN total_refs, unique_count,
                       CASE WHEN total_refs > 0 THEN (toFloat(total_refs - unique_count) / total_refs) * 100 ELSE 0 END AS dedup_percent
            """)
            structure_stats = result.single()

            return {
                "content": {
                    "unique_count": content_stats["unique_count"],
                    "total_references": content_stats["total_refs"],
                    "deduplication_percent": round(content_stats["dedup_percent"], 2),
                    "top_reused": top_content,
                },
                "structure": {
                    "unique_count": structure_stats["unique_count"],
                    "total_references": structure_stats["total_refs"],
                    "deduplication_percent": round(structure_stats["dedup_percent"], 2),
                    "top_reused": top_structures,
                },
            }


# Convenience functions for module-level access
def get_document(source_id: str, use_batch: bool = True) -> Optional[Dict]:
    """Get a document by source_id."""
    with HybridGraphQuery() as query:
        return query.get_document(source_id, use_batch=use_batch)


def search_content(key: str, value: str, limit: int = 100) -> List[str]:
    """Search for sources containing a key-value pair."""
    with HybridGraphQuery() as query:
        return query.search_content(key, value, limit)


def find_shared_structures(min_refs: int = 10) -> List[Dict]:
    """Find structures shared across multiple sources."""
    with HybridGraphQuery() as query:
        return query.find_shared_structures(min_refs)


def diff_sources(id1: str, id2: str) -> Dict:
    """Compare two sources."""
    with HybridGraphQuery() as query:
        return query.diff_sources(id1, id2)


def get_source_stats(source_id: str) -> Dict:
    """Get stats for a source."""
    with HybridGraphQuery() as query:
        return query.get_source_stats(source_id)


def get_stats() -> Dict:
    """Get overall database stats."""
    with HybridGraphQuery() as query:
        return query.get_stats()


if __name__ == "__main__":
    import json

    print("HybridGraph Query API")
    print("=" * 60)

    with HybridGraphQuery() as query:
        stats = query.get_stats()
        print(f"\nDatabase Statistics:")
        print(f"  Sources: {stats['sources']}")
        print(f"  Structures: {stats['structures']}")
        print(f"  Contents: {stats['contents']}")
        print(f"  Total nodes: {stats['total_nodes']}")
        print(f"  Total relationships: {stats['total_relationships']}")

        print(f"\nDeduplication Statistics:")
        dedup = query.get_deduplication_stats()
        print(f"  Content: {dedup['content']['deduplication_percent']}% deduplicated")
        print(f"  Structure: {dedup['structure']['deduplication_percent']}% deduplicated")

        if dedup['content']['top_reused']:
            print(f"\nTop reused content:")
            for c in dedup['content']['top_reused'][:5]:
                print(f"    {c['key']}: {c['value'][:30] if c['value'] else 'null'}... ({c['ref_count']}x)")
