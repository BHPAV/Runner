# Runner Project

A task execution framework with LIFO stack-based processing and Neo4j graph database integration.

## Quick Reference

| Command | Description |
|---------|-------------|
| `pip install -e .` | Install package in development mode |
| `pip install -e ".[dev]"` | Install with dev dependencies |
| `python -m runner.core.stack_runner -v start <task>` | Run a task |
| `pytest tests/` | Run test suite |

## Package Structure

```
src/runner/
├── core/               # Task execution engine
│   ├── stack_runner.py # LIFO stack execution
│   ├── runner.py       # Multi-worker executor
│   └── bootstrap.py    # Database initialization
├── tasks/
│   ├── converters/     # File format → JSON
│   ├── upload/         # Neo4j data ingestion
│   └── utilities/      # Discovery tools
├── hybridgraph/        # Deduplicated graph storage
│   ├── sync.py         # Incremental sync
│   ├── reader.py       # Document retrieval
│   ├── queries.py      # Query API
│   ├── health.py       # Health monitoring
│   ├── delete.py       # Source deletion
│   ├── gc.py           # Garbage collection
│   └── migrate.py      # Full migration
└── utils/              # Shared utilities
    ├── hashing.py      # Merkle/content hashing
    └── neo4j.py        # Connection helpers
```

## Documentation

- [docs/README.md](docs/README.md) - Overview and quick start
- [docs/stack-runner.md](docs/stack-runner.md) - Task execution engine
- [docs/neo4j-schema.md](docs/neo4j-schema.md) - Database schemas (detailed)
- [docs/graph-quick-ref.md](docs/graph-quick-ref.md) - Graph schemas with live stats (LLM-optimized)
- [docs/cypher-patterns.md](docs/cypher-patterns.md) - Common Cypher query patterns
- [docs/sync-system.md](docs/sync-system.md) - Sync between databases
- [docs/task-reference.md](docs/task-reference.md) - Available tasks
- [MIGRATION.md](MIGRATION.md) - Migration guide for package reorganization

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

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Type checking
mypy src/runner

# Linting
ruff check src/runner
```

## Environment

Create `.env` from `.env.example`:

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=jsongraph
TARGET_DB=hybridgraph
```

## Common Tasks

```bash
# Using the new package imports
python -c "from runner.utils.hashing import compute_content_hash; print(compute_content_hash('string', 'test', 'value'))"

# Run hybridgraph sync
python -m runner.hybridgraph.sync

# Health check
python -m runner.hybridgraph.health

# Read/reconstruct document from hybridgraph
python -m runner.hybridgraph.reader get <source_id> --pretty

# Delete a source
python -m runner.hybridgraph.delete <source_id>

# Garbage collection
python -m runner.hybridgraph.gc

# Full migration
python -m runner.hybridgraph.migrate --dry-run --limit 100

# Legacy standalone scripts (still work)
python stack_runner.py -v start upload_dual --params '{"json_path": "file.json"}'
python sync_to_hybrid_task.py
python read_from_hybrid.py get <source_id> --pretty
```

## Neo4j Databases

| Database | Description |
|----------|-------------|
| `jsongraph` | Flat storage with two schemas: `:Data` nodes (task outputs) and `:JsonDoc/:JsonNode` tree (knowledge graph) |
| `hybridgraph` | Deduplicated - `:Source`, `:Structure`, `:Content` nodes with Merkle hashes |

### jsongraph Data Sources

| Schema | Node Count | Description | Migration |
|--------|------------|-------------|-----------|
| `:Data` | ~46K | Task runner outputs (stack_*, run_*) | `migrate_to_hybrid.py` |
| `:JsonDoc/:JsonNode` | ~1.4M | Knowledge graph entities (persons, orgs, locations) | `migrate_jsondoc_to_hybrid.py` |
