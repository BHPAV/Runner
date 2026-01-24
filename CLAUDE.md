# Runner Project

A task execution framework with LIFO stack-based processing and Neo4j graph database integration.

## Quick Reference

| Command | Description |
|---------|-------------|
| `python bootstrap.py --seed` | Initialize database with default tasks |
| `python stack_runner.py -v start <task>` | Run a task |
| `python sync_to_hybrid_task.py` | Sync jsongraph → hybridgraph |

## Documentation

- [docs/README.md](docs/README.md) - Overview and quick start
- [docs/stack-runner.md](docs/stack-runner.md) - Task execution engine
- [docs/neo4j-schema.md](docs/neo4j-schema.md) - Database schemas (detailed)
- [docs/graph-quick-ref.md](docs/graph-quick-ref.md) - Graph schemas with live stats (LLM-optimized)
- [docs/cypher-patterns.md](docs/cypher-patterns.md) - Common Cypher query patterns
- [docs/sync-system.md](docs/sync-system.md) - Automatic sync between databases
- [docs/task-reference.md](docs/task-reference.md) - Available tasks and parameters

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Tasks DB (SQLite)  →  Stack Runner (LIFO)  →  Runs Dir (JSON) │
│                              ↓                                   │
│                          Neo4j                                   │
│              jsongraph ←──sync──→ hybridgraph                   │
│              (flat)              (deduplicated, 90% smaller)    │
└─────────────────────────────────────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `stack_runner.py` | Main task execution engine |
| `bootstrap.py` | Database initialization |
| `upload_dual_task.py` | Upload to both Neo4j databases |
| `sync_to_hybrid_task.py` | Incremental sync with cleanup |
| `migrate_to_hybrid.py` | Full migration script |
| `read_from_hybrid.py` | Read/reconstruct documents from hybridgraph |
| `delete_source_task.py` | Delete sources with ref_count management |
| `garbage_collect_task.py` | Remove orphaned nodes |
| `hybridgraph_health_task.py` | Health monitoring and integrity checks |
| `hybridgraph_queries.py` | Python query API module |

## Environment

Configured via `.env`:

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=jsongraph
```

## Common Tasks

```bash
# Upload JSON to both databases
python stack_runner.py -v start upload_dual --params '{"json_path": "file.json"}'

# Batch upload
python stack_runner.py -v start batch_upload_dual --params '{"file_paths": ["a.json", "b.json"]}'

# Sync unsynced documents
python stack_runner.py -v start sync_to_hybrid

# Continuous background sync
python stack_runner.py -v start periodic_sync --params '{"continuous": true}'

# Read/reconstruct document from hybridgraph
python read_from_hybrid.py get <source_id> --pretty

# Search for documents containing a value
python read_from_hybrid.py search <key> <value>

# Compare two documents
python read_from_hybrid.py diff <source_id1> <source_id2>

# Verify document integrity
python read_from_hybrid.py verify <source_id>

# Delete a source
python delete_source_task.py <source_id>

# Health check
python hybridgraph_health_task.py

# Garbage collection
python garbage_collect_task.py
```

## Neo4j Databases

| Database | Description |
|----------|-------------|
| `jsongraph` | Flat storage - one `:Data` node per JSON element |
| `hybridgraph` | Deduplicated - `:Source`, `:Structure`, `:Content` nodes with Merkle hashes |
