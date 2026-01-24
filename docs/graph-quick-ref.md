# Graph Quick Reference

Compact schema and statistics for jsongraph and hybridgraph Neo4j databases.

## At a Glance

| Database | Purpose | Total Nodes | Strategy |
|----------|---------|-------------|----------|
| jsongraph | Full JSON tree storage | ~1.45M | One node per JSON element |
| hybridgraph | Deduplicated storage | ~277K | Merkle-hashed, content-addressable |

**Deduplication ratio**: ~81% node reduction (from jsongraph to hybridgraph)

---

## jsongraph

### Node Types

| Label | Count | Purpose |
|-------|-------|---------|
| JsonNode | 1,365,270 | JSON objects, arrays, values |
| Data | 46,162 | Flattened view (sync-tracked) |
| JsonDoc | 24,292 | Document roots |
| Identifier | 7,580 | Extracted entities (emails, hostnames) |
| Schema | 2,259 | Object structure signatures |

### Relationships

| Type | Count | Pattern |
|------|-------|---------|
| HAS_CHILD | 1,255,344 | Parent object/array → child |
| INSTANCE_OF | 402,209 | Node → Schema |
| HAS_ITEM | 218,333 | Array → element |
| CONTAINS | 46,080 | Data → Data (flattened) |
| ROOT | 24,292 | JsonDoc → root node |
| HAS_IDENTIFIER | 8,572 | Node → Identifier |

### Key Indexes

- `Data.doc_id` - Document lookup
- `Data.sync_status` - Sync queue
- `JsonNode.node_id` - Node lookup
- `JsonValue.value` - Value search
- `Identifier.value` - Entity search

### Document Types (Top 5)

| Type | Source | Count |
|------|--------|-------|
| knowledge_person | knowledge_graph | 12,453 |
| knowledge_organization | knowledge_graph | 7,214 |
| knowledge_location | knowledge_graph | 4,586 |
| network_device | network_graph | 16 |

---

## hybridgraph

### Node Types

| Label | Count | Purpose |
|-------|-------|---------|
| Structure | 136,243 | Objects/arrays (Merkle-hashed) |
| Content | 108,472 | Leaf values (deduplicated) |
| Source | 24,349 | Document entry points |
| Identifier | 7,580 | Extracted entities for cross-doc linking |

### Relationships

| Type | Count | Pattern |
|------|-------|---------|
| HAS_VALUE | 417,731 | Structure → Content |
| CONTAINS | 356,391 | Structure → Structure |
| HAS_ROOT | 24,349 | Source → root Structure |
| HAS_IDENTIFIER | 7,835 | Content → Identifier |

### Key Properties

**Source**
```
source_id (indexed, unique)
source_type: "knowledge_person" | "knowledge_organization" | "document" | ...
node_count: original node count
```

**Structure**
```
merkle (indexed, unique): "m:" + sha256[:32]
kind: "object" | "array"
child_keys: ["sorted", "key", "list"]
ref_count: sources referencing this
```

**Content**
```
hash (indexed, unique): "c:" + sha256[:32]
kind: "string" | "number" | "boolean" | "null"
value_str / value_num / value_bool
ref_count: structures referencing this
```

**Identifier**
```
kind: "email" | "hostname" | "ip" | "mac" | "phone" | "user_id"
value (indexed): the identifier string
ref_count: content nodes referencing this
```

### Source Types

| Type | Count |
|------|-------|
| knowledge_person | 12,453 |
| knowledge_organization | 7,214 |
| knowledge_location | 4,586 |
| document | 57 |
| network_device | 16 |

### Identifier Types

| Kind | Count | References |
|------|-------|------------|
| email | 6,128 | 6,307 |
| hostname | 1,407 | 1,481 |
| ip | 16 | 16 |
| mac | 15 | 15 |
| user_id | 4 | 10 |

### Top Shared Content

| Key | Value | References |
|-----|-------|------------|
| high | (numeric) | 231,864 |
| low | (numeric) | 36,691 |
| type | "Person" | 29,790 |
| timeZoneId | (null) | 28,983 |

---

## Graph Topology

### jsongraph
```
JsonDoc ─ROOT─► JsonNode ─HAS_CHILD─► JsonNode ─HAS_CHILD─► JsonValue
                   │                     │
                   ├─INSTANCE_OF─► Schema
                   └─HAS_IDENTIFIER─► Identifier
```

### hybridgraph
```
Source ─HAS_ROOT─► Structure ─CONTAINS─► Structure ─CONTAINS─► ...
                       │                     │
                       └─HAS_VALUE─► Content ─HAS_IDENTIFIER─► Identifier
```

---

## Migration Status

| Source | Documents | Status |
|--------|-----------|--------|
| Data nodes (task outputs) | 57 | ✅ Synced |
| JsonDoc (knowledge graph) | 24,292 | ✅ Migrated |
| Identifiers | 7,580 | ✅ Migrated |
| **Total Sources** | **24,349** | ✅ Complete |

---

## MCP Tool Reference

| Tool | Database |
|------|----------|
| `mcp__jsongraph-neo4j-cypher__get_neo4j_schema` | jsongraph |
| `mcp__jsongraph-neo4j-cypher__read_neo4j_cypher` | jsongraph |
| `mcp__hybridgraph-neo4j-cypher__get_neo4j_schema` | hybridgraph |
| `mcp__hybridgraph-neo4j-cypher__read_neo4j_cypher` | hybridgraph |

---

## Common Queries

### Find documents by identifier
```cypher
MATCH (i:Identifier {kind: 'email', value: $email})
MATCH (c:Content)-[:HAS_IDENTIFIER]->(i)
MATCH (s:Structure)-[:HAS_VALUE]->(c)
MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..10]->(s)
RETURN DISTINCT src.source_id, src.source_type
```

### Find shared structures
```cypher
MATCH (s:Structure)
WHERE s.ref_count > 10
RETURN s.kind, s.key, s.child_keys, s.ref_count
ORDER BY s.ref_count DESC LIMIT 20
```

### Reconstruct document
```cypher
MATCH (src:Source {source_id: $id})-[:HAS_ROOT]->(root)
CALL apoc.path.subgraphAll(root, {
  relationshipFilter: 'CONTAINS>|HAS_VALUE>',
  maxLevel: 20
}) YIELD nodes, relationships
RETURN nodes, relationships
```
