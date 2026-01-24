# Graph Quick Reference

Compact schema and statistics for jsongraph and hybridgraph Neo4j databases.

## At a Glance

| Database | Purpose | Total Nodes | Strategy |
|----------|---------|-------------|----------|
| jsongraph | Full JSON tree storage | ~1.4M | One node per JSON element |
| hybridgraph | Deduplicated storage | ~5K | Merkle-hashed, content-addressable |

**Deduplication ratio**: ~99.6% node reduction

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
| Content | 2,829 | Leaf values (deduplicated) |
| Structure | 2,204 | Objects/arrays (Merkle-hashed) |
| Source | 57 | Document entry points |

### Relationships

| Type | Count | Pattern |
|------|-------|---------|
| HAS_VALUE | 9,083 | Structure → Content |
| CONTAINS | 5,016 | Structure → Structure |
| HAS_ROOT | 57 | Source → root Structure |

### Key Properties

**Source**
```
source_id (indexed, unique)
source_type: "document" | "api" | "database"
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

### Top Shared Content

| Key | Value | References |
|-----|-------|------------|
| source | "json_data" | 1,144 |
| success | "true" | 1,136 |
| uploaded | "true" | 936 |
| classes_count | "0" | 808 |

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
                       └─HAS_VALUE─► Content (shared, ref_count tracked)
```

---

## Sync Tracking

**jsongraph → hybridgraph sync status:**
- `Data.sync_status = NULL` → Never synced
- `Data.sync_status = "pending"` → Queued for sync
- `Data.sync_status = "synced"` → In hybridgraph

**Current:** All 46,162 Data nodes synced to 57 Sources (aggregated)

---

## MCP Tool Reference

| Tool | Database |
|------|----------|
| `mcp__jsongraph-neo4j-cypher__get_neo4j_schema` | jsongraph |
| `mcp__jsongraph-neo4j-cypher__read_neo4j_cypher` | jsongraph |
| `mcp__hybridgraph-neo4j-cypher__get_neo4j_schema` | hybridgraph |
| `mcp__hybridgraph-neo4j-cypher__read_neo4j_cypher` | hybridgraph |
