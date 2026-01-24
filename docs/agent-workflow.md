# Agent Workflow System

This document describes the agent-driven task execution system that enables AI agents to submit work requests through MCP (Model Context Protocol) and receive results through Neo4j graph databases.

## Table of Contents

- [Overview](#overview)
- [Components](#components)
- [MCP Server Tools](#mcp-server-tools)
- [TaskRequest Lifecycle](#taskrequest-lifecycle)
- [Cascade Rules](#cascade-rules)
- [APOC Triggers](#apoc-triggers)
- [Setup Guide](#setup-guide)
- [Usage Examples](#usage-examples)
- [Troubleshooting](#troubleshooting)

---

## Overview

The agent workflow system creates a separation of concerns between AI agents and task execution:

| Layer | Responsibility | Access |
|-------|---------------|--------|
| **Agent** | Query context, submit requests, retrieve results | MCP tools (read Neo4j, write TaskRequests) |
| **Request Queue** | Store pending work, track status | Neo4j :TaskRequest nodes |
| **Processor** | Execute tasks, update status | Full stack runner access |
| **Triggers** | Cascade automation, dependency resolution | APOC triggers in Neo4j |

### Why This Architecture?

1. **Security**: Agents cannot execute arbitrary code directly
2. **Auditability**: All requests are logged as graph nodes
3. **Scalability**: Multiple processors can run in parallel
4. **Continuity**: Cascade rules enable autonomous pipelines
5. **Idempotency**: Request IDs prevent duplicate execution

---

## Components

### 1. MCP Server (`runner.mcp.server`)

Exposes tools for agents to interact with the task system.

**Location**: `src/runner/mcp/server.py`

**Configuration** (`.mcp.json`):
```json
{
  "runner-mcp": {
    "type": "stdio",
    "command": "python",
    "args": ["-m", "runner.mcp.server"],
    "env": {
      "PYTHONPATH": "src",
      "RUNNER_DB": "tasks.db",
      "NEO4J_DATABASE": "hybridgraph"
    }
  }
}
```

### 2. Request Processor (`runner.processor.daemon`)

Background daemon that polls for pending requests and executes them.

**Location**: `src/runner/processor/daemon.py`

**Start Command**:
```bash
runner processor -v              # Verbose mode
runner processor --single        # Process one and exit
runner processor --stats         # Show queue statistics
```

**Configuration**:
| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `NEO4J_DATABASE` | hybridgraph | Database for TaskRequests |
| `RUNNER_DB` | tasks.db | SQLite database path |
| `RUNS_DIR` | ./runs | Output directory |

### 3. APOC Triggers (`runner.triggers.setup`)

Neo4j triggers that automate dependency resolution and cascade rules.

**Location**: `src/runner/triggers/setup.py`

**Install**:
```bash
runner triggers --install        # Install all triggers
runner triggers --status         # Check trigger status
runner triggers --remove         # Remove all triggers
```

### 4. Cascade Rules (`runner.triggers.cascade_rules`)

Configurable rules that automatically create TaskRequests when graph events occur.

**Location**: `src/runner/triggers/cascade_rules.py`

**Management**:
```bash
runner cascade list              # List all rules
runner cascade create --rule-id <id> --task <task_id>
runner cascade enable <rule_id>
runner cascade disable <rule_id>
runner cascade delete <rule_id>
```

---

## MCP Server Tools

### `submit_task_request`

Submit a task request to the execution queue.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `task_id` | string | Yes | Task to execute (e.g., "upload_dual") |
| `parameters` | object | No | Task parameters |
| `priority` | integer | No | 1-1000, higher = sooner (default: 100) |
| `request_id` | string | No | Idempotency key |
| `depends_on` | array | No | List of request_ids to wait for |

**Returns**:
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "created_at": "2026-01-25T10:30:00Z",
  "task_id": "upload_dual",
  "priority": 100,
  "is_new": true
}
```

**Example**:
```
submit_task_request(
  task_id="csv_to_json",
  parameters={"input_path": "data.csv", "output_path": "data.json"},
  priority=150
)
```

### `get_request_status`

Check the status of a task request.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `request_id` | string | Yes | The request ID to check |

**Returns**:
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_id": "csv_to_json",
  "status": "done",
  "priority": 150,
  "requester": "mcp:user",
  "created_at": "2026-01-25T10:30:00Z",
  "claimed_by": "hostname:12345",
  "claimed_at": "2026-01-25T10:30:05Z",
  "finished_at": "2026-01-25T10:30:15Z",
  "result_ref": "stack_550e8400",
  "has_outputs": true
}
```

### `get_task_result`

Retrieve the result of a completed task request.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `request_id` | string | Yes | The request ID |
| `include_trace` | boolean | No | Include full execution trace |

**Returns**:
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_id": "csv_to_json",
  "status": "done",
  "finished_at": "2026-01-25T10:30:15Z",
  "result_ref": "stack_550e8400",
  "output": {"rows_converted": 1500, "output_path": "data.json"},
  "context": {"variables": {"row_count": 1500}}
}
```

### `list_available_tasks`

List all tasks that can be submitted.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `filter` | string | No | Filter by task ID substring |
| `enabled_only` | boolean | No | Only show enabled tasks (default: true) |

**Returns**:
```json
{
  "tasks": [
    {
      "task_id": "csv_to_json",
      "task_type": "python",
      "default_parameters": {"delimiter": ","},
      "timeout_seconds": 300,
      "enabled": true
    }
  ],
  "count": 15,
  "filter": null
}
```

### `cancel_request`

Cancel a pending task request.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `request_id` | string | Yes | The request ID to cancel |

**Returns**:
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "cancelled",
  "message": "Request cancelled successfully"
}
```

### `list_pending_requests`

List requests in the queue.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | integer | No | Maximum results (default: 20) |
| `status` | string | No | Filter by status (default: "pending") |

**Returns**:
```json
{
  "requests": [
    {
      "request_id": "...",
      "task_id": "upload_dual",
      "priority": 100,
      "requester": "mcp:user",
      "created_at": "2026-01-25T10:30:00Z"
    }
  ],
  "returned": 5,
  "total": 5,
  "status_filter": "pending"
}
```

---

## TaskRequest Lifecycle

### Status State Machine

```
                    ┌─────────────────────────────────┐
                    │                                 │
                    ▼                                 │
┌─────────┐    ┌─────────┐    ┌───────────┐    ┌──────┐
│ pending │───▶│ claimed │───▶│ executing │───▶│ done │
└─────────┘    └─────────┘    └───────────┘    └──────┘
     │              │              │
     │              │              │
     ▼              ▼              ▼
┌───────────┐  ┌────────┐    ┌────────┐
│ cancelled │  │ failed │    │ failed │
└───────────┘  └────────┘    └────────┘
     ▲
     │
┌─────────┐
│ blocked │ (waiting for dependencies)
└─────────┘
```

### Status Definitions

| Status | Description |
|--------|-------------|
| `pending` | Ready for processing, waiting in queue |
| `blocked` | Waiting for dependent requests to complete |
| `claimed` | Processor has claimed, about to execute |
| `executing` | Task is currently running |
| `done` | Successfully completed |
| `failed` | Execution failed (see `error` field) |
| `cancelled` | Cancelled by user |

### Neo4j Schema

```cypher
// TaskRequest node
(:TaskRequest {
  request_id: "uuid",           // Unique identifier (idempotency key)
  task_id: "upload_dual",       // Task to execute
  parameters: '{"key": "val"}', // JSON parameters
  status: "pending",            // Current status
  priority: 100,                // Higher = sooner (1-1000)
  requester: "mcp:user",        // Who submitted
  created_at: datetime(),       // When submitted
  claimed_by: "host:pid",       // Which processor claimed
  claimed_at: datetime(),       // When claimed
  finished_at: datetime(),      // When completed
  result_ref: "stack_abc123",   // Link to output file
  error: null                   // Error message if failed
})

// Dependency relationship
(:TaskRequest)-[:DEPENDS_ON]->(:TaskRequest)

// Cascade trigger relationship
(:TaskRequest)-[:TRIGGERED_BY]->(:CascadeRule)

// Result link (optional)
(:TaskRequest)-[:PRODUCED]->(:Source)
```

### Indexes and Constraints

```cypher
// Unique constraint
CREATE CONSTRAINT task_request_id IF NOT EXISTS
FOR (r:TaskRequest) REQUIRE r.request_id IS UNIQUE;

// Query indexes
CREATE INDEX task_request_status_priority IF NOT EXISTS
FOR (r:TaskRequest) ON (r.status, r.priority);

CREATE INDEX task_request_requester IF NOT EXISTS
FOR (r:TaskRequest) ON (r.requester);

CREATE INDEX task_request_task_id IF NOT EXISTS
FOR (r:TaskRequest) ON (r.task_id);
```

---

## Cascade Rules

Cascade rules automatically create new TaskRequests when graph events occur.

### CascadeRule Schema

```cypher
(:CascadeRule {
  rule_id: "process_new_json",           // Unique identifier
  description: "Validate new JSON",       // Human description
  source_kind: "json",                    // Match Sources with this kind (null = all)
  task_id: "validate_json",               // Task to create
  parameter_template: '{"source_id": "$source.source_id"}',
  priority: 50,                           // Priority for created requests
  enabled: true,                          // Is rule active
  created_at: datetime()
})
```

### Parameter Templates

Templates support placeholder substitution:

| Placeholder | Replaced With |
|-------------|---------------|
| `$source.source_id` | The new Source node's source_id |
| `$source.kind` | The Source's kind property |

**Example Template**:
```json
{"source_id": "$source.source_id", "validate_schema": true}
```

### Creating Cascade Rules

```bash
# Create a rule that validates all new JSON sources
runner cascade create \
  --rule-id validate_json_sources \
  --task validate_json \
  --source-kind json \
  --parameters '{"source_id": "$source.source_id"}' \
  --priority 75

# Create a rule for all sources
runner cascade create \
  --rule-id index_all_sources \
  --task update_search_index \
  --parameters '{"source_id": "$source.source_id"}'

# Disable a rule temporarily
runner cascade disable validate_json_sources

# View triggered requests
runner cascade triggered validate_json_sources --limit 50
```

---

## APOC Triggers

Three APOC triggers power the automation:

### 1. `resolve_dependencies`

Unblocks requests when their dependencies complete.

**Fires**: When a TaskRequest status changes to 'done'

**Action**: Finds all 'blocked' requests that only depended on the completed request and sets them to 'pending'

```cypher
MATCH (waiting:TaskRequest)-[:DEPENDS_ON]->(completed:TaskRequest {status: 'done'})
WHERE waiting.status = 'blocked'
AND NOT EXISTS {
    MATCH (waiting)-[:DEPENDS_ON]->(other:TaskRequest)
    WHERE other.status <> 'done'
}
SET waiting.status = 'pending'
```

### 2. `cascade_on_source`

Creates new TaskRequests based on CascadeRules when Sources are created.

**Fires**: When a new :Source node is created

**Action**: Matches enabled CascadeRules and creates corresponding TaskRequests

```cypher
MATCH (rule:CascadeRule {enabled: true})
WHERE rule.source_kind IS NULL OR n.kind = rule.source_kind
CREATE (req:TaskRequest {
    request_id: randomUUID(),
    task_id: rule.task_id,
    parameters: /* template with substitutions */,
    status: 'pending',
    priority: rule.priority,
    requester: 'trigger:' + rule.rule_id
})
CREATE (req)-[:TRIGGERED_BY]->(rule)
```

### 3. `mark_sync_pending`

Marks new Data nodes for synchronization.

**Fires**: When a new :Data node is created in jsongraph

**Action**: Sets `sync_status = 'pending'` for incremental sync detection

---

## Setup Guide

### Prerequisites

1. **Neo4j** with APOC plugin installed
2. **Python 3.10+** with package installed
3. **Environment** configured (`.env` file)

### Step 1: Install Package

```bash
cd /path/to/Runner
pip install -e ".[dev]"
```

### Step 2: Configure Environment

Create `.env`:
```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=jsongraph
TARGET_DB=hybridgraph
```

### Step 3: Install Schema

```bash
# Add TaskRequest schema to hybridgraph
runner schema
```

### Step 4: Install APOC Triggers

```bash
# Check APOC is available
runner triggers --status

# Install triggers
runner triggers --install
```

### Step 5: Start Processor Daemon

```bash
# Start in foreground (for testing)
runner processor -v

# Or run in background
runner processor &

# Or use nohup for persistence
nohup runner processor > processor.log 2>&1 &
```

### Step 6: Verify Setup

```bash
# Check processor stats
runner processor --stats

# List cascade rules
runner cascade list

# Check trigger status
runner triggers --status
```

---

## Usage Examples

### Example 1: Simple Task Submission

Agent submits a CSV conversion task:

```python
# Via MCP tool
result = submit_task_request(
    task_id="csv_to_json",
    parameters={
        "input_path": "/data/sales.csv",
        "output_path": "/data/sales.json"
    }
)
# Returns: {"request_id": "abc123", "status": "pending", ...}

# Poll for completion
while True:
    status = get_request_status(request_id="abc123")
    if status["status"] in ("done", "failed"):
        break
    time.sleep(5)

# Get results
result = get_task_result(request_id="abc123")
print(result["output"])  # {"rows_converted": 1500, ...}
```

### Example 2: Dependent Tasks

Agent creates a pipeline where task B depends on task A:

```python
# Submit first task
result_a = submit_task_request(
    task_id="download_data",
    parameters={"url": "https://example.com/data.csv"}
)

# Submit dependent task (will be blocked until A completes)
result_b = submit_task_request(
    task_id="process_data",
    parameters={"input_ref": result_a["request_id"]},
    depends_on=[result_a["request_id"]]
)

# Only need to poll B - it won't run until A is done
status = get_request_status(request_id=result_b["request_id"])
# status["status"] == "blocked" (until A completes)
```

### Example 3: Cascade-Triggered Pipeline

Set up automatic validation for all new JSON sources:

```bash
# Create cascade rule
runner cascade create \
  --rule-id auto_validate \
  --task validate_json \
  --source-kind json \
  --parameters '{"source_id": "$source.source_id", "strict": true}'
```

Now when any task writes a new `:Source {kind: 'json'}` node, a validation TaskRequest is automatically created.

### Example 4: Batch Processing

Submit multiple related tasks:

```python
# Submit batch of uploads
request_ids = []
for file in files:
    result = submit_task_request(
        task_id="upload_dual",
        parameters={"json_path": file},
        priority=50
    )
    request_ids.append(result["request_id"])

# Submit aggregation task that depends on all uploads
submit_task_request(
    task_id="aggregate_results",
    depends_on=request_ids,
    priority=100  # Higher priority so it runs as soon as possible
)
```

---

## Troubleshooting

### Request Stuck in "pending"

1. Check processor is running:
   ```bash
   runner processor --stats
   ```

2. Check for dependency issues:
   ```cypher
   MATCH (r:TaskRequest {request_id: $id})-[:DEPENDS_ON]->(dep)
   WHERE dep.status <> 'done'
   RETURN dep.request_id, dep.status
   ```

3. Restart processor with verbose logging:
   ```bash
   runner processor -v
   ```

### Request Failed

1. Check error message:
   ```cypher
   MATCH (r:TaskRequest {request_id: $id})
   RETURN r.error
   ```

2. Check execution trace in runs directory:
   ```bash
   cat runs/stack_<id>.json
   ```

### Cascade Rules Not Firing

1. Verify triggers are installed:
   ```bash
   runner triggers --status
   ```

2. Check rule is enabled:
   ```bash
   runner cascade get <rule_id>
   ```

3. Verify APOC triggers are enabled in `neo4j.conf`:
   ```
   apoc.trigger.enabled=true
   ```

### Processor Not Claiming Requests

1. Check Neo4j connection:
   ```bash
   python -c "from runner.utils.neo4j import get_driver; get_driver()"
   ```

2. Verify request exists:
   ```cypher
   MATCH (r:TaskRequest {status: 'pending'})
   RETURN count(r)
   ```

3. Check for worker ID conflicts in logs

---

## Monitoring

### Queue Depth Query

```cypher
MATCH (r:TaskRequest)
RETURN r.status, count(r) as count
ORDER BY count DESC
```

### Processing Rate (Last Hour)

```cypher
MATCH (r:TaskRequest)
WHERE r.finished_at > datetime() - duration('PT1H')
AND r.status = 'done'
RETURN count(r) as completed_last_hour
```

### Failed Requests

```cypher
MATCH (r:TaskRequest {status: 'failed'})
RETURN r.request_id, r.task_id, r.error, r.finished_at
ORDER BY r.finished_at DESC
LIMIT 10
```

### Cascade Rule Activity

```cypher
MATCH (req:TaskRequest)-[:TRIGGERED_BY]->(rule:CascadeRule)
RETURN rule.rule_id, count(req) as triggered_count
ORDER BY triggered_count DESC
```

---

## API Reference

See also:
- [unified-agent-runner-architecture.md](unified-agent-runner-architecture.md) - Full architecture specification
- [stack-runner.md](stack-runner.md) - Task execution details
- [neo4j-schema.md](neo4j-schema.md) - Complete database schemas
