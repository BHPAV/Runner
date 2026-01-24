# Runner System Documentation

A task execution framework with LIFO stack-based processing and Neo4j graph database integration.

## Overview

The Runner system provides:

- **Stack Runner**: LIFO task execution with monadic context accumulation
- **Neo4j Integration**: Dual-database architecture (jsongraph + hybridgraph)
- **File Conversion Pipeline**: Convert various file formats to JSON for graph ingestion
- **Automatic Sync**: Real-time and incremental sync between databases

## Documentation Index

| Document | Description |
|----------|-------------|
| [Stack Runner](stack-runner.md) | Task execution engine with LIFO processing |
| [Neo4j Schema](neo4j-schema.md) | Database schemas for jsongraph and hybridgraph |
| [Sync System](sync-system.md) | Automatic synchronization between databases |
| [Task Reference](task-reference.md) | Available tasks and their parameters |

## Quick Start

### 1. Initialize the Database

```bash
python bootstrap.py --seed
```

### 2. Run a Task

```bash
# Single task
python stack_runner.py -v start hello_cli

# With parameters
python stack_runner.py -v start upload_dual \
  --params '{"json_path": "data.json"}'
```

### 3. Upload Data to Neo4j

```bash
# Dual-write (both databases)
python stack_runner.py -v start upload_dual \
  --params '{"json_path": "myfile.json"}'

# Batch upload
python stack_runner.py -v start batch_upload_dual \
  --params '{"file_paths": ["file1.json", "file2.json"]}'
```

### 4. Sync Databases

```bash
# Manual sync
python sync_to_hybrid_task.py --limit 100

# Via stack runner
python stack_runner.py -v start sync_to_hybrid
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         RUNNER SYSTEM                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   Tasks DB   │───▶│ Stack Runner │───▶│   Runs Dir   │       │
│  │  (SQLite)    │    │   (LIFO)     │    │   (JSON)     │       │
│  └──────────────┘    └──────┬───────┘    └──────────────┘       │
│                             │                                    │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                      NEO4J                                │   │
│  │  ┌─────────────────┐         ┌─────────────────┐         │   │
│  │  │   jsongraph     │ ──sync──▶│  hybridgraph   │         │   │
│  │  │  (flat Data)    │         │ (deduplicated)  │         │   │
│  │  └─────────────────┘         └─────────────────┘         │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TASK_DB` | `./tasks.db` | SQLite database path |
| `RUNS_DIR` | `./runs` | Output directory for execution logs |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password` | Neo4j password |
| `NEO4J_DATABASE` | `jsongraph` | Default Neo4j database |

## File Structure

```
Runner/
├── bootstrap.py              # Database initialization
├── stack_runner.py           # Main task execution engine
├── schema.sql                # SQLite schema for tasks
├── schema_stack.sql          # SQLite schema for stack execution
├── .env                      # Environment configuration
│
├── # Upload Tasks
├── upload_jsongraph_task.py  # Upload to jsongraph only
├── upload_dual_task.py       # Upload to both databases
├── batch_upload_json_task.py # Batch upload orchestrator
│
├── # Sync Tasks
├── sync_to_hybrid_task.py    # Incremental sync
├── setup_auto_sync.py        # Configure automatic sync
├── migrate_to_hybrid.py      # Full migration script
│
├── # File Converters
├── csv_to_json_task.py
├── yaml_to_json_task.py
├── xml_to_json_task.py
├── markdown_to_json_task.py
├── text_to_json_task.py
├── python_to_json_task.py
├── code_to_json_task.py
│
├── runs/                     # Execution output logs
└── docs/                     # Documentation
```
