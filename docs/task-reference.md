# Task Reference

Complete reference of available tasks in the Runner system.

## Core Tasks

### hello_cli

Simple CLI echo task for testing.

```bash
python stack_runner.py -v start hello_cli --params '{"greeting": "World"}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `greeting` | string | "World" | Text to echo |

### hello_python

Python test task.

```bash
python stack_runner.py -v start hello_python --params '{"name": "Alice"}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | "Anonymous" | Name to greet |

---

## Upload Tasks

### upload_jsongraph

Upload JSON to jsongraph only (flat storage).

```bash
python stack_runner.py -v start upload_jsongraph \
  --params '{"json_path": "data.json", "doc_id": "my_doc"}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `json_path` | string | — | Path to JSON file |
| `json_data` | object | — | Direct JSON object (alternative to json_path) |
| `doc_id` | string | (auto) | Document identifier |
| `neo4j_uri` | string | env | Neo4j connection URI |
| `neo4j_user` | string | env | Neo4j username |
| `neo4j_password` | string | env | Neo4j password |
| `neo4j_database` | string | "jsongraph" | Target database |
| `max_content_length` | int | 5000 | Max string length before truncation |

### upload_dual

Upload JSON to both jsongraph and hybridgraph simultaneously.

```bash
python stack_runner.py -v start upload_dual \
  --params '{"json_path": "data.json"}'
```

Same parameters as `upload_jsongraph`, plus:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source_db` | string | "jsongraph" | Source database name |
| `target_db` | string | "hybridgraph" | Target database name |

### batch_upload_json

Batch upload multiple JSON files to jsongraph.

```bash
python stack_runner.py -v start batch_upload_json \
  --params '{"file_paths": ["file1.json", "file2.json"]}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_paths` | string[] | — | List of JSON file paths |

### batch_upload_dual

Batch upload multiple JSON files to both databases.

```bash
python stack_runner.py -v start batch_upload_dual \
  --params '{"file_paths": ["file1.json", "file2.json"]}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_paths` | string[] | — | List of JSON file paths |

---

## Sync Tasks

### sync_to_hybrid

Incremental sync from jsongraph to hybridgraph.

```bash
python stack_runner.py -v start sync_to_hybrid --params '{"limit": 50}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 100 | Max documents to sync per run |

**Output:**
```json
{
  "documents_synced": 45,
  "content_created": 14,
  "content_reused": 1234,
  "structure_created": 6,
  "structure_reused": 567,
  "errors": []
}
```

### periodic_sync

Self-rescheduling periodic sync task.

```bash
python stack_runner.py -v start periodic_sync \
  --params '{"continuous": true, "interval_seconds": 60}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `continuous` | bool | false | If true, reschedules itself after completion |
| `interval_seconds` | int | 60 | Delay between runs |
| `run_number` | int | 0 | Internal counter (auto-incremented) |

---

## File Converter Tasks

These tasks convert various file formats to JSON for graph ingestion.

### csv_to_json_task

Convert CSV file to JSON.

```bash
python stack_runner.py -v start csv_to_json_task \
  --params '{"file_path": "data.csv"}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | — | Path to CSV file |

### yaml_to_json_task

Convert YAML file to JSON.

```bash
python stack_runner.py -v start yaml_to_json_task \
  --params '{"file_path": "config.yaml"}'
```

### xml_to_json_task

Convert XML file to JSON.

```bash
python stack_runner.py -v start xml_to_json_task \
  --params '{"file_path": "data.xml"}'
```

### markdown_to_json_task

Convert Markdown file to structured JSON.

```bash
python stack_runner.py -v start markdown_to_json_task \
  --params '{"file_path": "README.md"}'
```

### text_to_json_task

Convert plain text file to JSON.

```bash
python stack_runner.py -v start text_to_json_task \
  --params '{"file_path": "notes.txt"}'
```

### python_to_json_task

Parse Python file and extract structure (functions, classes, imports).

```bash
python stack_runner.py -v start python_to_json_task \
  --params '{"file_path": "script.py"}'
```

### code_to_json_task

Generic code file to JSON converter.

```bash
python stack_runner.py -v start code_to_json_task \
  --params '{"file_path": "app.js"}'
```

---

## Batch Conversion Tasks

### find_unrecorded_files_task

Find files not yet recorded in the graph.

```bash
python stack_runner.py -v start find_unrecorded_files_task \
  --params '{"search_path": "~/Documents", "extensions": [".csv", ".json"], "limit": 20}'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `search_path` | string | "~/Downloads" | Directory to search |
| `extensions` | string[] | [".csv", ".yaml", ...] | File extensions to find |
| `limit` | int | 20 | Max files to return |

### batch_convert_files_task

Batch convert multiple files to JSON.

```bash
python stack_runner.py -v start batch_convert_files_task
```

Uses `file_paths` from context (set by `find_unrecorded_files_task`).

---

## Stack Demonstration Tasks

### stack_planner

Demonstrates task decomposition with push_tasks.

```bash
python stack_runner.py -v start stack_planner \
  --params '{"problem": "build a feature", "steps": ["analyze", "implement", "verify"]}'
```

### stack_recursive

Demonstrates recursive task execution with context accumulation.

```bash
python stack_runner.py -v start stack_recursive --params '{"n": 5}'
```

---

## Task Registration

To register a new task:

```python
import sqlite3
import json

conn = sqlite3.connect('./tasks.db')
conn.execute("""
    INSERT OR REPLACE INTO tasks
    (task_id, task_type, code, parameters_json, working_dir, env_json, timeout_seconds, enabled)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""", (
    "my_task",
    "python_file",           # cli | python | python_file | typescript
    "my_script.py",
    json.dumps({"default_param": "value"}),
    None,                    # working directory
    json.dumps({}),          # environment variables
    300,                     # timeout in seconds
    1                        # enabled
))
conn.commit()
```

Or use bootstrap.py:

```bash
python bootstrap.py --seed  # Seeds default tasks
python bootstrap.py --queue my_task --queue-params '{"param": "value"}'
```
