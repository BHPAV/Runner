# Runner Project

A task execution framework with LIFO stack-based processing and Neo4j graph database integration.

## Quick Reference

| Command | Description |
|---------|-------------|
| `pip install -e .` | Install package in development mode |
| `pip install -e ".[dev]"` | Install with dev dependencies |
| `python -m runner.core.stack_runner -v start <task>` | Run a task |
| `pytest tests/` | Run test suite |

## Project Structure

```
Runner/
├── src/runner/              # Main package
│   ├── core/                # Task execution engine
│   │   ├── stack_runner.py  # LIFO stack execution
│   │   ├── runner.py        # Multi-worker executor
│   │   └── bootstrap.py     # Database initialization
│   ├── tasks/
│   │   ├── converters/      # File format → JSON (csv, xml, yaml, etc.)
│   │   ├── upload/          # Neo4j data ingestion
│   │   └── utilities/       # Discovery tools
│   ├── hybridgraph/         # Deduplicated graph storage
│   │   ├── sync.py          # Incremental sync
│   │   ├── reader.py        # Document retrieval
│   │   ├── queries.py       # Query API (HybridGraphQuery)
│   │   ├── health.py        # Health monitoring
│   │   ├── delete.py        # Source deletion
│   │   ├── gc.py            # Garbage collection
│   │   └── migrate.py       # Full migration
│   ├── utils/               # Shared utilities
│   │   ├── hashing.py       # Merkle/content hashing
│   │   └── neo4j.py         # Connection helpers
│   └── db/
│       └── migrations/      # One-time migration scripts
├── scripts/                 # Utility scripts
│   └── setup_auto_sync.py   # Configure automatic sync
├── tests/                   # Test suite (67 tests)
├── docs/                    # Documentation
├── runs/                    # Task output directory
├── pyproject.toml           # Package configuration
└── requirements.txt         # Dependencies
```

## Documentation

- [docs/README.md](docs/README.md) - Overview and quick start
- [docs/stack-runner.md](docs/stack-runner.md) - Task execution engine
- [docs/neo4j-schema.md](docs/neo4j-schema.md) - Database schemas (detailed)
- [docs/graph-quick-ref.md](docs/graph-quick-ref.md) - Graph schemas with live stats
- [docs/cypher-patterns.md](docs/cypher-patterns.md) - Common Cypher query patterns
- [docs/sync-system.md](docs/sync-system.md) - Sync between databases
- [docs/task-reference.md](docs/task-reference.md) - Available tasks
- [docs/hybridgraph-improvements.md](docs/hybridgraph-improvements.md) - Technical improvements
- [MIGRATION.md](MIGRATION.md) - Package reorganization guide

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

## Common Commands

```bash
# Hybridgraph operations
python -m runner.hybridgraph.sync              # Incremental sync
python -m runner.hybridgraph.health            # Health check
python -m runner.hybridgraph.reader list       # List sources
python -m runner.hybridgraph.reader get <id>   # Reconstruct document
python -m runner.hybridgraph.delete <id>       # Delete a source
python -m runner.hybridgraph.gc                # Garbage collection
python -m runner.hybridgraph.migrate           # Full migration

# Task runner
python -m runner.core.stack_runner -v start <task>
python -m runner.core.stack_runner -v start upload_dual --params '{"json_path": "file.json"}'

# Setup auto-sync
python scripts/setup_auto_sync.py --method all

# Using utilities directly
python -c "from runner.utils.hashing import compute_content_hash; print(compute_content_hash('string', 'key', 'value'))"
```

## Neo4j Databases

| Database | Description |
|----------|-------------|
| `jsongraph` | Flat storage - `:Data` nodes and `:JsonDoc/:JsonNode` trees |
| `hybridgraph` | Deduplicated - `:Source`, `:Structure`, `:Content` with Merkle hashes |

## Key Modules

| Module | Purpose |
|--------|---------|
| `runner.core.stack_runner` | LIFO task execution with context accumulation |
| `runner.hybridgraph.queries` | `HybridGraphQuery` class for document operations |
| `runner.hybridgraph.sync` | Incremental jsongraph → hybridgraph sync |
| `runner.utils.hashing` | `compute_content_hash`, `compute_merkle_hash`, `encode_value_for_hash` |
| `runner.utils.neo4j` | `get_config`, `get_driver`, `get_session` |
