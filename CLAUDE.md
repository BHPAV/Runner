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
│   ├── mcp/                 # MCP server for agent integration
│   │   └── server.py        # Task submission tools
│   ├── processor/           # Request processor daemon
│   │   └── daemon.py        # Bridges MCP requests to stack runner
│   ├── triggers/            # APOC trigger management
│   │   ├── setup.py         # Install/remove triggers
│   │   └── cascade_rules.py # Cascade rule CRUD
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
├── tests/                   # Test suite
├── docs/                    # Documentation
├── runs/                    # Task output directory
├── pyproject.toml           # Package configuration
└── requirements.txt         # Dependencies
```

## Documentation

### Agent Workflow (New)
- [docs/agent-workflow.md](docs/agent-workflow.md) - **MCP tools, TaskRequest lifecycle, cascade rules**
- [docs/system-diagrams.md](docs/system-diagrams.md) - **Visual diagrams of system interactions**
- [docs/unified-agent-runner-architecture.md](docs/unified-agent-runner-architecture.md) - Full architecture specification

### Core System
- [docs/README.md](docs/README.md) - Overview and quick start
- [docs/stack-runner.md](docs/stack-runner.md) - Task execution engine
- [docs/task-reference.md](docs/task-reference.md) - Available tasks

### Graph Databases
- [docs/neo4j-schema.md](docs/neo4j-schema.md) - Database schemas (detailed)
- [docs/graph-quick-ref.md](docs/graph-quick-ref.md) - Graph schemas with live stats
- [docs/cypher-patterns.md](docs/cypher-patterns.md) - Common Cypher query patterns
- [docs/sync-system.md](docs/sync-system.md) - Sync between databases
- [docs/hybridgraph-improvements.md](docs/hybridgraph-improvements.md) - Technical improvements

### Migration
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

## Agent-Driven Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Agent (Claude)                                                  │
│  └── MCP Tools: runner-mcp, jsongraph-mcp, hybridgraph-mcp      │
│                      │                                           │
│                      ▼ submit_task_request()                     │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  :TaskRequest nodes (Neo4j hybridgraph)                     ││
│  │  status: pending → claimed → executing → done               ││
│  └─────────────────────────────────────────────────────────────┘│
│                      │                                           │
│                      ▼ processor daemon polls                    │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Stack Runner (LIFO execution)                              ││
│  │  → Results to jsongraph/hybridgraph                         ││
│  └─────────────────────────────────────────────────────────────┘│
│                      │                                           │
│                      ▼ APOC triggers                             │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Cascade Rules → New :TaskRequest (continuous loop)         ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Graph System Interactions

```
                              ┌─────────────────────────────────────────────┐
                              │              AGENT LAYER                     │
                              │                                              │
                              │   jsongraph-mcp ──┐                         │
                              │   (read-only)     │                         │
                              │                   ├──▶ Claude Agent          │
                              │   hybridgraph-mcp │                         │
                              │   (read-only)  ◀──┘                         │
                              │                   │                          │
                              │   runner-mcp ─────┘  submit_task_request()  │
                              │   (write :TaskRequest)                       │
                              │                                              │
                              └──────────────────────┬──────────────────────┘
                                                     │
                    ┌────────────────────────────────┼────────────────────────────────┐
                    │                                │                                │
                    ▼                                ▼                                │
┌───────────────────────────────────┐  ┌───────────────────────────────────┐         │
│          jsongraph                │  │          hybridgraph              │         │
│                                   │  │                                   │         │
│  ┌─────────────────────────────┐ │  │ ┌─────────────────────────────┐  │         │
│  │ :Data                       │ │  │ │ :Source                     │  │         │
│  │   └── :JsonDoc              │ │  │ │   └── :Structure (Merkle)   │  │         │
│  │         └── :JsonNode       │ │  │ │         └── :Content        │  │         │
│  │              └── ...        │ │  │ │              └── ...        │  │         │
│  └─────────────────────────────┘ │  │ └─────────────────────────────┘  │         │
│                                   │  │                                   │         │
│  sync_status: 'pending'          │  │ ┌─────────────────────────────┐  │         │
│         │                        │  │ │ :TaskRequest                │◀─┼─────────┘
│         │   incremental sync     │  │ │   status: pending/done/...  │  │
│         └────────────────────────┼──┼▶│   └── :DEPENDS_ON           │  │
│                                   │  │ │   └── :TRIGGERED_BY        │  │
│                                   │  │ └─────────────────────────────┘  │
│                                   │  │                                   │
│                                   │  │ ┌─────────────────────────────┐  │
│                                   │  │ │ :CascadeRule                │  │
│                                   │  │ │   source_kind: "json"       │  │
│                                   │  │ │   task_id: "validate"       │  │
│                                   │  │ └─────────────────────────────┘  │
│                                   │  │                                   │
│                                   │  │ ════════════════════════════════ │
│                                   │  │ APOC TRIGGERS:                   │
│                                   │  │ • resolve_dependencies           │
│                                   │  │ • cascade_on_source              │
│                                   │  │ • mark_sync_pending              │
│                                   │  │ ════════════════════════════════ │
└───────────────────────────────────┘  └───────────────────────────────────┘
                    ▲                                ▲
                    │                                │
                    │         ┌──────────────────────┘
                    │         │
                    │         │
┌───────────────────┴─────────┴───────────────────────────────────────────┐
│                        EXECUTION LAYER                                   │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    Request Processor Daemon                         │ │
│  │                                                                     │ │
│  │   1. Poll :TaskRequest {status: 'pending'}                         │ │
│  │   2. Claim atomically (status → 'claimed')                         │ │
│  │   3. Execute via Stack Runner                                       │ │
│  │   4. Update status (→ 'done' or 'failed')                          │ │
│  │   5. APOC triggers fire (cascade, resolve)                          │ │
│  │                                                                     │ │
│  └───────────────────────────────┬────────────────────────────────────┘ │
│                                  │                                       │
│                                  ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      Stack Runner (LIFO)                            │ │
│  │                                                                     │ │
│  │   tasks.db (SQLite) ──▶ Task Queue ──▶ Subprocess ──▶ runs/*.json  │ │
│  │                                              │                      │ │
│  │                                              │ upload_dual          │ │
│  │                                              ▼                      │ │
│  │                              Writes to jsongraph AND hybridgraph    │ │
│  │                                                                     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

See [docs/system-diagrams.md](docs/system-diagrams.md) for detailed flow diagrams.

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
# Agent workflow setup
runner schema                                   # Install TaskRequest schema
runner triggers --install                       # Install APOC triggers
runner processor -v                             # Start processor daemon

# Agent workflow operations
runner cascade list                             # List cascade rules
runner cascade create --rule-id my_rule --task validate_json
runner cascade enable my_rule
runner mcp                                      # Start MCP server (for testing)

# Task runner (direct execution)
runner stack start <task>
runner stack start upload_dual --params '{"json_path": "file.json"}'

# Hybridgraph operations
runner sync --limit 100                         # Incremental sync
runner health --full                            # Health check
runner reader list                              # List sources
runner reader get <id>                          # Reconstruct document
runner delete <id>                              # Delete a source
runner gc                                       # Garbage collection
runner migrate                                  # Full migration

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
| `runner.mcp.server` | MCP server for agent task submission |
| `runner.processor.daemon` | Request processor (Neo4j → stack runner bridge) |
| `runner.triggers.setup` | APOC trigger installation |
| `runner.triggers.cascade_rules` | Cascade rule management |
| `runner.hybridgraph.queries` | `HybridGraphQuery` class for document operations |
| `runner.hybridgraph.sync` | Incremental jsongraph → hybridgraph sync |
| `runner.utils.hashing` | `compute_content_hash`, `compute_merkle_hash`, `encode_value_for_hash` |
| `runner.utils.neo4j` | `get_config`, `get_driver`, `get_session` |
