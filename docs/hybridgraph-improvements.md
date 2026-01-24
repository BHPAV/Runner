# Hybridgraph System Improvements

A comprehensive review of the hybridgraph system with proposed fixes and enhancements.

---

## Executive Summary

The hybridgraph system provides content-addressable storage with Merkle hashing for JSON documents. While the architecture is sound, there are several **critical data integrity issues** and **performance bottlenecks** that need attention:

| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 2 | Hash collisions, orphan deletion bug |
| High | 3 | Root merkle validation, ref_count race condition, code duplication |
| Medium | 4 | N+1 queries, inconsistent path limits, incomplete health checks |
| Low | 3 | Missing audit trail, source_type unused, documentation gaps |

---

## Critical Issues

### 1. Hash Collision Vulnerability

**Location:** `src/runner/hybridgraph/sync.py:139-148`

**Problem:** The hash computation converts different types to the same string representation:

```python
# Current implementation
value = node["value_str"]
if value is None and node["value_num"] is not None:
    value = str(node["value_num"])  # 1.0 → "1.0"
elif value is None and node["value_bool"] is not None:
    value = str(node["value_bool"]).lower()  # True → "true"
elif value is None:
    value = "null"
```

**Collision Examples:**
- Boolean `true` and string `"true"` produce identical hashes
- Number `1.0` and string `"1.0"` produce identical hashes
- Null value and string `"null"` produce identical hashes

**Impact:** Different values incorrectly deduplicate to the same Content node, corrupting data integrity.

**Fix:** Include type prefix in hash computation:

```python
def compute_content_hash(kind: str, key: str, value: str) -> str:
    # Include kind in the value to prevent type collisions
    # Format: "kind|key|kind:value" ensures type safety
    content = f"{kind}|{key}|{kind}:{value}"
    return "c:" + hashlib.sha256(content.encode()).hexdigest()[:32]
```

**Files to Update:**
- `src/runner/utils/hashing.py`
- `src/runner/hybridgraph/sync.py`
- `src/runner/hybridgraph/migrate.py`
- `sync_to_hybrid_task.py`
- `upload_dual_task.py`
- `migrate_to_hybrid.py`

**Migration Required:** Yes - rehash all existing Content nodes

---

### 2. Orphan Cleanup Deletes Referenced Nodes

**Location:** `src/runner/hybridgraph/sync.py:383-400`

**Problem:** The `cleanup_orphaned_nodes()` function ignores `ref_count`:

```python
# Current - DANGEROUS
result = session.run("""
    MATCH (s:Structure)
    WHERE NOT ()-[:HAS_ROOT]->(s) AND NOT ()-[:CONTAINS]->(s)
    DETACH DELETE s
    RETURN count(*) AS deleted
""")
```

This deletes ANY Structure without incoming relationships, even if `ref_count > 0`.

**Scenario:**
1. Document A creates Structure X (ref_count=1)
2. A concurrent operation deletes the HAS_ROOT relationship temporarily
3. Cleanup runs and deletes Structure X
4. Document A is now corrupted

**Fix:** Add ref_count check:

```python
result = session.run("""
    MATCH (s:Structure)
    WHERE NOT ()-[:HAS_ROOT]->(s)
      AND NOT ()-[:CONTAINS]->(s)
      AND (s.ref_count IS NULL OR s.ref_count = 0)
    DETACH DELETE s
    RETURN count(*) AS deleted
""")
```

---

## High Priority Issues

### 3. Missing root_merkle Validation

**Location:** `src/runner/hybridgraph/sync.py:352-364`

**Problem:** No validation when `root_merkle` is None:

```python
root_merkle = hashes.get("/root")  # Could be None!
# ...
session.run("""
    MERGE (source:Source {source_id: $doc_id})
    ...
    MATCH (root:Structure {merkle: $root_merkle})  # Silent failure if NULL
    MERGE (source)-[:HAS_ROOT]->(root)
""", root_merkle=root_merkle)
```

**Impact:** Source nodes created without HAS_ROOT relationship, causing orphaned sources.

**Fix:**

```python
root_merkle = hashes.get("/root")
if not root_merkle:
    return {"error": f"Failed to compute root merkle for {doc_id}"}
```

---

### 4. ref_count Race Condition on Re-sync

**Location:** `src/runner/hybridgraph/sync.py:228-232, 257`

**Problem:** Re-sync decrements old ref_counts, then increments new ones separately:

```python
# Step 1: Decrement old
decrement_old_ref_counts(session, old_nodes["structures"], old_nodes["contents"])

# Step 2: Increment new (later in the code)
ON MATCH SET c.ref_count = c.ref_count + 1
```

If a node exists in both old and new sets, the operations are:
1. Decrement: 2 → 1
2. Increment: 1 → 2 (net zero change, correct)

**But with race conditions:**
- Another sync could read ref_count=1 between steps
- GC could delete the node if ref_count hits 0

**Fix:** Use a single atomic operation:

```python
def sync_document_atomic(driver, source_db, target_db, doc_id):
    # Compute old and new hashes
    old_hashes = get_existing_hashes(source_id)
    new_hashes = compute_document_hashes(data)

    # Diff: what to decrement, what to increment
    to_decrement = old_hashes - new_hashes
    to_increment = new_hashes - old_hashes
    unchanged = old_hashes & new_hashes  # No ref_count change needed

    # Single transaction
    with session.begin_transaction() as tx:
        # Decrement only nodes being removed
        if to_decrement:
            tx.run("UNWIND $hashes AS h MATCH (n {hash: h}) SET n.ref_count = n.ref_count - 1",
                   hashes=list(to_decrement))

        # Create/increment only new nodes
        if to_increment:
            tx.run("UNWIND $nodes AS n MERGE (c:Content {hash: n.hash}) ON CREATE SET ... ON MATCH SET ref_count = ref_count + 1",
                   nodes=to_increment_nodes)

        tx.commit()
```

---

### 5. Hash Function Duplication

**Locations:** 6 files with independent implementations

| File | Functions |
|------|-----------|
| `src/runner/utils/hashing.py` | `compute_content_hash`, `compute_merkle_hash` |
| `src/runner/hybridgraph/sync.py` | `compute_content_hash`, `compute_merkle_hash` |
| `src/runner/hybridgraph/migrate.py` | `compute_content_hash`, `compute_merkle_hash` |
| `sync_to_hybrid_task.py` | `compute_content_hash`, `compute_merkle_hash` |
| `upload_dual_task.py` | `compute_content_hash`, `compute_merkle_hash` |
| `migrate_to_hybrid.py` | `compute_content_hash`, `compute_merkle_hash` |

**Risk:** Changes to one file don't propagate to others, causing hash inconsistencies.

**Fix:** Remove all duplicates, import from single source:

```python
# In all files:
from runner.utils.hashing import compute_content_hash, compute_merkle_hash
```

---

## Medium Priority Issues

### 6. N+1 Query Problem in Document Reconstruction

**Location:** `src/runner/hybridgraph/queries.py:86-160`

**Problem:** Recursive reconstruction executes 2-3 queries per node:

```python
def _reconstruct_node(self, session, merkle: str) -> Any:
    # Query 1: Get node info
    result = session.run("MATCH (s:Structure {merkle: $merkle}) ...")

    # Query 2: Get child structures
    result = session.run("MATCH (s:Structure {merkle: $merkle})-[:CONTAINS]->...")

    # Query 3: Get child content
    result = session.run("MATCH (s:Structure {merkle: $merkle})-[:HAS_VALUE]->...")

    # Recursive calls for each child
    for child in children:
        self._reconstruct_node(session, child["merkle"])  # More queries!
```

**Impact:** Document with 1000 nodes = 2000-3000 queries.

**Fix:** Single batch query with `apoc.path.subgraphAll`:

```python
def get_document_batch(self, source_id: str) -> Optional[Dict]:
    with self.driver.session(database=self.database) as session:
        # Single query gets entire subgraph
        result = session.run("""
            MATCH (src:Source {source_id: $source_id})-[:HAS_ROOT]->(root:Structure)
            CALL apoc.path.subgraphAll(root, {
                relationshipFilter: 'CONTAINS>|HAS_VALUE>',
                maxLevel: 100
            }) YIELD nodes, relationships

            WITH root, nodes, relationships
            UNWIND nodes AS n
            WITH root, n, relationships,
                 CASE WHEN n:Structure THEN 'structure' ELSE 'content' END AS node_type
            RETURN root.merkle AS root_merkle,
                   collect(CASE WHEN node_type = 'structure'
                           THEN {merkle: n.merkle, kind: n.kind, key: n.key}
                           ELSE {hash: n.hash, kind: n.kind, key: n.key,
                                 value_str: n.value_str, value_num: n.value_num,
                                 value_bool: n.value_bool}
                           END) AS nodes,
                   [r IN relationships | {
                       type: type(r),
                       start: startNode(r).merkle,
                       end: COALESCE(endNode(r).merkle, endNode(r).hash),
                       key: r.key
                   }] AS rels
        """, source_id=source_id)

        # Reconstruct in memory from batch result
        return self._build_tree_from_batch(result.single())
```

---

### 7. Inconsistent Path Depth Limits

**Locations:**

| File | Limit | Usage |
|------|-------|-------|
| `queries.py:213` | 20 | search_content |
| `queries.py:308,312` | 50 | diff_sources |
| `delete.py:69,80` | 100 | delete_source |
| `sync.py:170` | 100 | get_existing_source_nodes |

**Problem:** Deep JSON structures (>20 levels) won't be properly searched.

**Fix:** Standardize on 100 and document the limit:

```python
# In src/runner/hybridgraph/__init__.py
MAX_DEPTH = 100  # Maximum nesting depth for JSON documents
```

---

### 8. Incomplete ref_count Health Check

**Location:** `src/runner/hybridgraph/health.py:146-194`

**Problem:** Only validates Structure ref_counts against HAS_ROOT:

```cypher
MATCH (s:Structure)
OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(s)
WITH s, count(DISTINCT src) AS actual_root_refs
WHERE s.ref_count <> actual_root_refs
```

This misses:
- Child structures (via CONTAINS)
- Content nodes (via HAS_VALUE)

**Fix:** Comprehensive validation:

```cypher
-- Structure ref_count = number of Sources whose tree includes this structure
MATCH (s:Structure)
OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..100]->(s)
WITH s, count(DISTINCT src) AS actual_refs
WHERE s.ref_count <> actual_refs
RETURN s.merkle, s.ref_count AS stored, actual_refs AS computed

-- Content ref_count validation
MATCH (c:Content)
OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..100]->(:Structure)-[:HAS_VALUE]->(c)
WITH c, count(DISTINCT src) AS actual_refs
WHERE c.ref_count <> actual_refs
RETURN c.hash, c.ref_count AS stored, actual_refs AS computed
```

---

## Low Priority Issues

### 9. No Audit Trail for Document Changes

**Current:** `node_count` is overwritten on re-sync with no history.

**Enhancement:** Add versioning:

```cypher
(:Source {
  source_id: "doc1",
  node_count: 150,
  version: 3,
  versions: [{v: 1, count: 100, at: "2024-01-01"}, {v: 2, count: 120, at: "2024-02-01"}]
})
```

---

### 10. source_type Always "document"

**Current:** Schema supports `document | api | database | web` but all syncs set `document`.

**Enhancement:** Auto-detect or parameterize:

```python
def sync_document(driver, source_db, target_db, doc_id, source_type="document"):
    # ...
    session.run("""
        MERGE (source:Source {source_id: $doc_id})
        SET source.source_type = $source_type
    """, source_type=source_type)
```

---

## Implementation Plan

### Phase 1: Critical Fixes (Immediate)

| Task | Files | Complexity |
|------|-------|------------|
| Fix hash collision vulnerability | 6 files + migration | High |
| Add ref_count check to orphan cleanup | sync.py | Low |
| Add root_merkle validation | sync.py | Low |

### Phase 2: Data Integrity (High Priority)

| Task | Files | Complexity |
|------|-------|------------|
| Consolidate hash functions | All hybridgraph files | Medium |
| Fix ref_count race condition | sync.py | Medium |
| Add comprehensive ref_count validation | health.py | Medium |

### Phase 3: Performance (Medium Priority)

| Task | Files | Complexity |
|------|-------|------------|
| Batch document reconstruction | queries.py, reader.py | High |
| Standardize path depth limits | All query files | Low |

### Phase 4: Enhancements (Low Priority)

| Task | Files | Complexity |
|------|-------|------------|
| Add audit trail | sync.py, schema | Medium |
| Dynamic source_type | sync.py, upload_dual | Low |

---

## Migration Script for Hash Fix

When implementing the hash collision fix, existing data must be migrated:

```python
def migrate_hashes():
    """Rehash all Content nodes with type-safe encoding."""
    with driver.session(database="hybridgraph") as session:
        # 1. Create new nodes with corrected hashes
        session.run("""
            MATCH (c:Content)
            WITH c,
                 'c:' + substring(
                     apoc.util.sha256(c.kind + '|' + c.key + '|' + c.kind + ':' +
                         COALESCE(c.value_str, toString(c.value_num),
                                  toLower(toString(c.value_bool)), 'null')),
                     0, 32
                 ) AS new_hash
            WHERE new_hash <> c.hash

            // Create new node with corrected hash
            MERGE (new:Content {hash: new_hash})
            ON CREATE SET new = c, new.hash = new_hash

            // Update relationships
            WITH c, new
            MATCH (s:Structure)-[r:HAS_VALUE]->(c)
            MERGE (s)-[:HAS_VALUE {key: r.key}]->(new)

            // Mark old for deletion
            SET c:_ToDelete
        """)

        # 2. Delete old nodes
        session.run("MATCH (c:_ToDelete) DETACH DELETE c")
```

---

## Verification Checklist

After implementing fixes, verify:

- [ ] Hash collision test: `{"val": "true"}` vs `{"val": true}` have different hashes
- [ ] Orphan cleanup respects ref_count
- [ ] Re-sync with unchanged content doesn't alter ref_counts
- [ ] Health check catches all ref_count mismatches
- [ ] Document reconstruction uses ≤5 queries regardless of size
- [ ] All hash functions import from single source
