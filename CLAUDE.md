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
- [docs/neo4j-schema.md](docs/neo4j-schema.md) - Database schemas
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
| `sync_to_hybrid_task.py` | Incremental sync |
| `migrate_to_hybrid.py` | Full migration script |

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
```

## Neo4j Databases

| Database | Description |
|----------|-------------|
| `jsongraph` | Flat storage - one `:Data` node per JSON element |
| `hybridgraph` | Deduplicated - `:Source`, `:Structure`, `:Content` nodes with Merkle hashes |
