# Cypher Query Patterns

Quick reference for querying jsongraph and hybridgraph databases.

## jsongraph (Flat Storage)

### Schema Summary
```
(:JsonDoc)-[:ROOT]->(:JsonNode)-[:HAS_CHILD|HAS_ITEM]->(:JsonNode|JsonValue)
(:JsonNode)-[:INSTANCE_OF]->(:Schema)
(:JsonNode|JsonValue)-[:HAS_IDENTIFIER]->(:Identifier)
(:Data)-[:CONTAINS]->(:Data)  # Flattened view
```

### Common Queries

```cypher
# List documents by type
MATCH (d:JsonDoc)
RETURN d.doc_type, d.source, count(*) AS count
ORDER BY count DESC

# Get document tree
MATCH (d:JsonDoc {doc_id: $id})-[:ROOT]->(root)
MATCH path = (root)-[:HAS_CHILD|HAS_ITEM*0..10]->(n)
RETURN path

# Find by identifier (email, hostname, user_id)
MATCH (i:Identifier {kind: "email", value: $email})
MATCH (n)-[:HAS_IDENTIFIER]->(i)
MATCH (doc:JsonDoc)-[:ROOT]->()-[:HAS_CHILD*0..10]->(n)
RETURN DISTINCT doc.doc_id

# Get Data nodes for a document
MATCH (d:Data {doc_id: $id})
RETURN d.path, d.key, d.kind,
       COALESCE(d.value_str, d.value_num, d.value_bool) AS value
ORDER BY d.path

# Find unsynced documents
MATCH (d:Data)
WHERE d.sync_status IS NULL OR d.sync_status = 'pending'
RETURN DISTINCT d.doc_id

# Search by schema
MATCH (s:Schema {name: $schema_name})
MATCH (n)-[:INSTANCE_OF]->(s)
MATCH (doc:JsonDoc)-[:ROOT]->()-[:HAS_CHILD*0..10]->(n)
RETURN DISTINCT doc.doc_id, doc.doc_type
```

---

## hybridgraph (Deduplicated)

### Schema Summary
```
(:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS {key}]->(:Structure)
                                   -[:HAS_VALUE {key}]->(:Content)
```

### Key Properties
| Node | Primary Key | Important Fields |
|------|-------------|------------------|
| Source | source_id | source_type, node_count, name |
| Structure | merkle | kind (object/array), child_keys, ref_count |
| Content | hash | kind, value_str/value_num/value_bool, ref_count |

### Common Queries

```cypher
# List all sources
MATCH (s:Source)
RETURN s.source_id, s.source_type, s.node_count, s.name
ORDER BY s.node_count DESC

# Reconstruct document as tree
MATCH (src:Source {source_id: $id})-[:HAS_ROOT]->(root)
CALL apoc.path.subgraphAll(root, {
  relationshipFilter: 'CONTAINS>|HAS_VALUE>',
  maxLevel: 20
}) YIELD nodes, relationships
RETURN nodes, relationships

# Find sources containing a value
MATCH (c:Content {key: $key, value_str: $value})
MATCH path = (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS|HAS_VALUE*0..15]->(c)
RETURN DISTINCT src.source_id, src.name

# Most referenced content (deduplication wins)
MATCH (c:Content)
WHERE c.ref_count > 10
RETURN c.key, c.kind,
       COALESCE(c.value_str, toString(c.value_num), toString(c.value_bool)) AS value,
       c.ref_count
ORDER BY c.ref_count DESC LIMIT 20

# Most referenced structures
MATCH (s:Structure)
WHERE s.ref_count > 5
RETURN s.kind, s.key, s.child_keys, s.ref_count
ORDER BY s.ref_count DESC LIMIT 20

# Compare two sources (find differences)
MATCH (s1:Source {source_id: $id1})-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..10]->(struct1)
MATCH (s2:Source {source_id: $id2})-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..10]->(struct2)
WITH collect(DISTINCT struct1.merkle) AS m1, collect(DISTINCT struct2.merkle) AS m2
RETURN [x IN m1 WHERE NOT x IN m2] AS only_in_first,
       [x IN m2 WHERE NOT x IN m1] AS only_in_second

# Get root structure fields
MATCH (src:Source {source_id: $id})-[:HAS_ROOT]->(root:Structure)
OPTIONAL MATCH (root)-[r:CONTAINS|HAS_VALUE]->(child)
RETURN r.key AS key, labels(child)[0] AS type,
       CASE WHEN child:Content THEN COALESCE(child.value_str, child.value_num) END AS value

# Find orphaned nodes (for garbage collection)
MATCH (s:Structure)
WHERE s.ref_count = 0 AND NOT ()-[:HAS_ROOT|CONTAINS]->(s)
RETURN count(s) AS orphaned_structures

MATCH (c:Content)
WHERE c.ref_count = 0 AND NOT ()-[:HAS_VALUE]->(c)
RETURN count(c) AS orphaned_content

# Storage statistics
MATCH (s:Source) WITH count(s) AS sources
MATCH (st:Structure) WITH sources, count(st) AS structures
MATCH (c:Content) WITH sources, structures, count(c) AS content
RETURN sources, structures, content, structures + content AS total_nodes
```

---

## Cross-Database Patterns

### Verify sync status
```cypher
# In jsongraph: count synced vs unsynced
MATCH (d:Data)
RETURN d.sync_status, count(DISTINCT d.doc_id) AS docs

# In hybridgraph: count sources
MATCH (s:Source)
RETURN count(s) AS sources, sum(s.node_count) AS total_nodes
```

### Match document between databases
```cypher
# jsongraph: get doc_id
MATCH (d:JsonDoc) RETURN d.doc_id LIMIT 5

# hybridgraph: source_id often derived from doc_id
MATCH (s:Source) WHERE s.source_id CONTAINS $partial_id
RETURN s.source_id, s.name
```
