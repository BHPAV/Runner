# Neo4j Schema

The system uses two Neo4j databases with different storage strategies.

## Database Overview

| Database | Strategy | Purpose |
|----------|----------|---------|
| `jsongraph` | Flat storage | Original JSON structure, one node per JSON element |
| `hybridgraph` | Deduplicated | Content-addressable with Merkle hashes, 90%+ smaller |

## jsongraph Schema (Flat)

### Node: `:Data`

Each JSON element becomes a `:Data` node:

```cypher
(:Data {
  doc_id: "document_identifier",
  path: "/root/nested/property",
  kind: "string",           // string | number | boolean | null | object | array
  key: "property",          // Property name or array index
  value_str: "text value",  // For strings
  value_num: 123,           // For numbers
  value_bool: true,         // For booleans
  sync_status: "synced"     // Sync tracking: null | pending | synced
})
```

### Relationship: `:CONTAINS`

Parent-child relationships in the JSON structure:

```cypher
(:Data {path: "/root"})-[:CONTAINS]->(:Data {path: "/root/child"})
```

### Example

For this JSON:
```json
{"name": "Alice", "age": 30}
```

Creates:
```
(:Data {path: "/root", kind: "object"})
  ├─[:CONTAINS]→(:Data {path: "/root/name", kind: "string", value_str: "Alice"})
  └─[:CONTAINS]→(:Data {path: "/root/age", kind: "number", value_num: 30})
```

---

## hybridgraph Schema (Deduplicated)

A content-addressable Merkle graph that deduplicates values and structures.

### Node: `:Source`

Represents a data source (document, API, database, etc.):

```cypher
(:Source {
  source_id: "unique_identifier",
  source_type: "document",        // document | api | database | web
  name: "display_name",
  node_count: 123,                // Original node count
  ingested_at: datetime(),
  last_synced: datetime()
})
```

### Node: `:Structure`

Container nodes (objects/arrays) with Merkle hashes:

```cypher
(:Structure {
  merkle: "m:abc123def456",       // Merkle hash (PRIMARY KEY)
  kind: "object",                  // object | array
  key: "propertyName",
  child_keys: ["key1", "key2"],   // Sorted child keys (for objects)
  child_count: 5,
  ref_count: 42                    // Number of sources using this structure
})
```

The `merkle` hash is computed as:
```
merkle = sha256(kind + "|" + key + "|" + sorted(child_merkle_hashes).join("|"))
```

### Node: `:Content`

Leaf values with content-addressable hashes:

```cypher
(:Content {
  hash: "c:789xyz012abc",          // Content hash (PRIMARY KEY)
  kind: "string",                   // string | number | boolean | null
  key: "propertyName",
  value_str: "text value",
  value_num: null,
  value_bool: null,
  ref_count: 156                    // Number of structures referencing this
})
```

The `hash` is computed as:
```
hash = sha256(kind + "|" + key + "|" + value)
```

### Relationships

```cypher
// Source to root structure
(:Source)-[:HAS_ROOT]->(:Structure)

// Structure to child structure
(:Structure)-[:CONTAINS {key: "childName"}]->(:Structure)

// Structure to leaf value
(:Structure)-[:HAS_VALUE {key: "propertyName"}]->(:Content)
```

### Visual Example

```
┌─────────────────────────────────────────────────────────────────┐
│  :Source                                                        │
│  source_id: "doc1"                                              │
└─────────────┬───────────────────────────────────────────────────┘
              │ :HAS_ROOT
              ▼
┌─────────────────────────────────────────────────────────────────┐
│  :Structure                                                     │
│  merkle: "m:root_abc"                                           │
│  kind: "object"                                                 │
│  child_keys: ["name", "age"]                                    │
└─────────────┬───────────────────┬───────────────────────────────┘
              │ :HAS_VALUE        │ :HAS_VALUE
              │ key: "name"       │ key: "age"
              ▼                   ▼
┌─────────────────────┐  ┌─────────────────────┐
│  :Content           │  │  :Content           │
│  hash: "c:str_alice"│  │  hash: "c:num_30"   │
│  kind: "string"     │  │  kind: "number"     │
│  value_str: "Alice" │  │  value_num: 30      │
│  ref_count: 42      │  │  ref_count: 156     │◀── Shared by 156
└─────────────────────┘  └─────────────────────┘   structures!
```

---

## Indexes & Constraints

### jsongraph

```cypher
CREATE INDEX data_doc_id FOR (d:Data) ON (d.doc_id);
CREATE INDEX data_path FOR (d:Data) ON (d.path);
CREATE INDEX data_sync_status FOR (d:Data) ON (d.sync_status);
```

### hybridgraph

```cypher
-- Constraints (unique keys)
CREATE CONSTRAINT source_id_unique FOR (s:Source) REQUIRE s.source_id IS UNIQUE;
CREATE CONSTRAINT content_hash_unique FOR (c:Content) REQUIRE c.hash IS UNIQUE;
CREATE CONSTRAINT structure_merkle_unique FOR (s:Structure) REQUIRE s.merkle IS UNIQUE;

-- Indexes
CREATE INDEX content_lookup FOR (c:Content) ON (c.kind, c.key);
CREATE INDEX content_value_str FOR (c:Content) ON (c.value_str);
CREATE INDEX content_value_num FOR (c:Content) ON (c.value_num);
CREATE INDEX structure_kind FOR (s:Structure) ON (s.kind, s.key);
CREATE INDEX source_type FOR (s:Source) ON (s.source_type);
```

---

## Query Examples

### Find Documents with Specific Value (hybridgraph)

```cypher
MATCH (c:Content {key: "status", value_str: "done"})
MATCH (s:Structure)-[:HAS_VALUE]->(c)
MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..10]->(s)
RETURN DISTINCT src.source_id
```

### Find Shared Structures

```cypher
MATCH (s:Structure)
WHERE s.ref_count > 10
RETURN s.kind, s.key, s.child_keys, s.ref_count
ORDER BY s.ref_count DESC
LIMIT 20
```

### Reconstruct Document Tree

```cypher
MATCH (src:Source {source_id: $doc_id})-[:HAS_ROOT]->(root:Structure)
CALL apoc.path.subgraphAll(root, {
  relationshipFilter: 'CONTAINS>|HAS_VALUE>',
  maxLevel: 20
}) YIELD nodes, relationships
RETURN nodes, relationships
```

### Diff Two Documents

```cypher
MATCH (s1:Source {source_id: $doc1})-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..10]->(struct1:Structure)
MATCH (s2:Source {source_id: $doc2})-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..10]->(struct2:Structure)
WITH collect(DISTINCT struct1.merkle) AS merkles1,
     collect(DISTINCT struct2.merkle) AS merkles2
RETURN
  [m IN merkles1 WHERE NOT m IN merkles2] AS only_in_doc1,
  [m IN merkles2 WHERE NOT m IN merkles1] AS only_in_doc2
```

---

## Storage Comparison

| Metric | jsongraph | hybridgraph | Reduction |
|--------|-----------|-------------|-----------|
| Typical nodes | 45,990 | 5,057 | 89% |
| Unique values | — | 2,815 | — |
| Unique structures | — | 2,192 | — |

The hybridgraph achieves ~90% reduction through:

1. **Content deduplication**: Same values share one `:Content` node
2. **Structure deduplication**: Identical JSON objects share one `:Structure` node
3. **Merkle hashing**: Enables fast comparison and change detection
