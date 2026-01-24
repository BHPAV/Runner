# Unified Agent-Runner Architecture

## Executive Summary

This document describes the architecture for a unified system where AI agents interact with the Runner task execution framework through MCP (Model Context Protocol). The design separates concerns:

- **Agents**: Write-only access to submit task requests via MCP
- **Runner**: Processes requests and outputs results to Neo4j graphs
- **Triggers**: Neo4j APOC triggers create continuous execution loops

## Goals

1. Agents only write to Neo4j to gather context and submit requests
2. Requests go through an MCP server to a processing queue
3. A separate process triggers the Runner to execute tasks
4. Results written to jsongraph/hybridgraph trigger downstream tasks
5. System runs continuously with minimal manual intervention

---

## System Comparison

### Current Runner System

| Component | Implementation | Strengths | Limitations |
|-----------|----------------|-----------|-------------|
| Execution | LIFO stack with monadic context | Context accumulation, depth-first | No external submission API |
| Task Storage | SQLite `tasks` table | ACID, simple | Not queryable by agents |
| Queue | SQLite `stack_queue` | Multi-worker leasing | Requires direct DB access |
| Results | Neo4j jsongraph/hybridgraph | Queryable, deduplicated | Read-only MCP access |
| MCP | Read-only Neo4j servers | Safe for agents | Cannot submit work |

### Alternative System (cycler/direct + agent-table-cycle)

| Component | Implementation | Strengths | Limitations |
|-----------|----------------|-----------|-------------|
| Execution | FIFO queue + LLM planning | AI reasoning, service requests | Two separate systems |
| Task Storage | SQLite or Neo4j | Flexible | Split architecture |
| Queue | SQLite leases or Neo4j nodes | Graph-native possible | Not unified |
| Results | JSON files or Neo4j decomposition | Full audit trail | Different formats |
| MCP | None | N/A | No agent integration |

### Key Pattern from agent-table-cycle

The **Service Request Pattern** is the critical insight:

```
Agent → emits ServiceRequest → External Executor → ToolOutput → Next Task
```

Agents never execute tools directly. They emit requests that are processed externally. This provides:
- Full audit trail
- Retry without re-running agent
- Batching and deduplication
- Clear security boundaries

---

## Unified Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AGENT LAYER                                    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Claude Agent (Claude Code, API, etc.)                              │   │
│  │                                                                      │   │
│  │  Available MCP Tools:                                                │   │
│  │  ├── jsongraph-neo4j-cypher (read-only, existing)                   │   │
│  │  ├── hybridgraph-neo4j-cypher (read-only, existing)                 │   │
│  │  └── runner-mcp (NEW - submit requests, check status)               │   │
│  │                                                                      │   │
│  │  Agent can:                                                          │   │
│  │  • Query existing data for context                                   │   │
│  │  • Submit :TaskRequest nodes                                         │   │
│  │  • Poll for request completion                                       │   │
│  │  • Retrieve results                                                  │   │
│  └──────────────────────────────┬──────────────────────────────────────┘   │
│                                 │                                           │
└─────────────────────────────────┼───────────────────────────────────────────┘
                                  │ MCP: submit_task_request()
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           REQUEST QUEUE (Neo4j)                             │
│                                                                             │
│  Database: hybridgraph (or dedicated 'requests' database)                   │
│                                                                             │
│  (:TaskRequest {                                                            │
│    request_id: "uuid",           // Idempotency key                        │
│    task_id: "upload_dual",       // Which task to run                      │
│    parameters: '{...}',          // JSON parameters                        │
│    status: "pending",            // pending|claimed|executing|done|failed  │
│    priority: 100,                // Higher = sooner                        │
│    requester: "agent:claude",    // Who submitted                          │
│    created_at: datetime(),                                                  │
│    claimed_by: null,             // Worker ID when claimed                 │
│    claimed_at: null,                                                        │
│    finished_at: null,                                                       │
│    result_ref: null,             // Link to output (source_id or stack_id) │
│    error: null                   // Error message if failed                │
│  })                                                                         │
│                                                                             │
│  Indexes:                                                                   │
│  • UNIQUE (request_id)                                                      │
│  • INDEX (status, priority DESC, created_at ASC)                           │
│                                                                             │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
                    ▼                           ▼
        ┌───────────────────┐       ┌───────────────────────┐
        │   APOC Trigger    │       │   Request Processor   │
        │   (Push Model)    │       │   (Poll Model)        │
        │                   │       │                       │
        │ ON CREATE         │       │ Loop:                 │
        │ :TaskRequest      │       │ 1. Query pending      │
        │ → notify          │       │ 2. Claim atomically   │
        │   processor       │       │ 3. Execute stack      │
        └───────────────────┘       │ 4. Update status      │
                                    │ 5. Link results       │
                                    └───────────┬───────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXECUTION LAYER                                   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Stack Runner (existing: src/runner/core/stack_runner.py)           │   │
│  │                                                                      │   │
│  │  • create_stack(conn, task_id, parameters, request_id)              │   │
│  │  • run_stack_to_completion(conn, stack_id)                          │   │
│  │  • LIFO execution with context accumulation                         │   │
│  │  • Subprocess execution (CLI, Python, TypeScript)                   │   │
│  │                                                                      │   │
│  │  Task Types:                                                         │   │
│  │  • converters (csv, xml, yaml → JSON)                               │   │
│  │  • upload (jsongraph, dual)                                          │   │
│  │  • utilities (find_unrecorded, batch operations)                    │   │
│  │  • custom (user-defined tasks)                                       │   │
│  └──────────────────────────────┬──────────────────────────────────────┘   │
│                                 │                                           │
└─────────────────────────────────┼───────────────────────────────────────────┘
                                  │ Task outputs data
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RESULT STORAGE (Neo4j)                            │
│                                                                             │
│  ┌─────────────────────────┐    ┌─────────────────────────────────────┐    │
│  │      jsongraph          │    │         hybridgraph                 │    │
│  │                         │    │                                     │    │
│  │  :Data nodes            │◄───│  :Source, :Structure, :Content     │    │
│  │  :JsonDoc, :JsonNode    │sync│  Merkle-hashed, deduplicated       │    │
│  │  Flat storage           │    │  ~90% smaller                       │    │
│  └─────────────────────────┘    └─────────────────────────────────────┘    │
│                                                                             │
│  On data write:                                                             │
│  • sync_status = 'pending' (for incremental sync)                          │
│  • APOC triggers fire for cascade                                           │
│                                                                             │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TRIGGER LAYER (APOC)                              │
│                                                                             │
│  Trigger 1: result_ready                                                    │
│  ─────────────────────────                                                  │
│  ON UPDATE :TaskRequest SET status = 'done'                                 │
│  → Find dependent :TaskRequest nodes                                        │
│  → SET status = 'pending' (unblocks them)                                   │
│                                                                             │
│  Trigger 2: data_cascade                                                    │
│  ─────────────────────────                                                  │
│  ON CREATE :Source (hybridgraph)                                            │
│  → Check for registered cascade rules                                       │
│  → Create new :TaskRequest if pattern matches                               │
│                                                                             │
│  Trigger 3: sync_required                                                   │
│  ─────────────────────────                                                  │
│  ON CREATE :Data (jsongraph) WHERE NOT synced                               │
│  → Mark sync_status = 'pending'                                             │
│  → (Picked up by incremental sync process)                                  │
│                                                                             │
│  Cascade Rules (:CascadeRule nodes):                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  (:CascadeRule {                                                    │   │
│  │    rule_id: "process_new_json",                                     │   │
│  │    match_pattern: "(:Source {kind: 'json'})",                       │   │
│  │    task_id: "validate_json",                                        │   │
│  │    parameter_template: '{"source_id": "$source.id"}',               │   │
│  │    enabled: true                                                    │   │
│  │  })                                                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  │ New :TaskRequest created
                                  ▼
                        ┌─────────────────────┐
                        │  CONTINUOUS LOOP    │
                        │  Back to Request    │
                        │  Queue              │
                        └─────────────────────┘
```

---

## Component Specifications

### 1. Runner MCP Server

**Purpose**: Allow agents to submit task requests and monitor execution

**Transport**: stdio (same as existing Neo4j MCP servers)

**Tools Exposed**:

| Tool | Parameters | Returns | Description |
|------|------------|---------|-------------|
| `submit_task_request` | `task_id`, `parameters`, `priority`, `request_id` | `{request_id, status}` | Create :TaskRequest node |
| `get_request_status` | `request_id` | `{status, result_ref, error}` | Check request status |
| `get_task_result` | `request_id` or `result_ref` | `{output, context}` | Retrieve execution results |
| `list_available_tasks` | `filter` (optional) | `[{task_id, description, parameters}]` | List registered tasks |
| `cancel_request` | `request_id` | `{success, message}` | Cancel pending request |

**Implementation**: Python using `mcp` package

```python
# src/runner/mcp/server.py
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("runner-mcp")

@app.tool()
async def submit_task_request(
    task_id: str,
    parameters: dict = None,
    priority: int = 100,
    request_id: str = None
) -> dict:
    """Submit a task request to the execution queue."""
    ...
```

### 2. Request Schema (Neo4j)

**Database**: hybridgraph (or separate `requests` database)

**Node Label**: `:TaskRequest`

```cypher
// Constraints
CREATE CONSTRAINT task_request_id IF NOT EXISTS
FOR (r:TaskRequest) REQUIRE r.request_id IS UNIQUE;

// Indexes
CREATE INDEX task_request_status IF NOT EXISTS
FOR (r:TaskRequest) ON (r.status, r.priority, r.created_at);

CREATE INDEX task_request_requester IF NOT EXISTS
FOR (r:TaskRequest) ON (r.requester);
```

**Status State Machine**:

```
pending → claimed → executing → done
    │         │          │
    │         │          └──→ failed
    │         └──────────────→ failed (timeout)
    └────────────────────────→ cancelled
```

**Relationships**:

```cypher
// Request depends on another request completing
(r1:TaskRequest)-[:DEPENDS_ON]->(r2:TaskRequest)

// Request produced this result
(r:TaskRequest)-[:PRODUCED]->(s:Source)

// Cascade rule triggered this request
(r:TaskRequest)-[:TRIGGERED_BY]->(rule:CascadeRule)
```

### 3. Request Processor

**Purpose**: Bridge between Neo4j request queue and Stack Runner

**Implementation**: Python daemon with poll loop

```python
# src/runner/processor/daemon.py

class RequestProcessor:
    def __init__(self, neo4j_driver, sqlite_conn, worker_id):
        self.driver = neo4j_driver
        self.conn = sqlite_conn
        self.worker_id = worker_id

    def claim_request(self) -> Optional[dict]:
        """Atomically claim next pending request."""
        query = """
        MATCH (r:TaskRequest {status: 'pending'})
        WHERE NOT EXISTS {
            MATCH (r)-[:DEPENDS_ON]->(dep:TaskRequest)
            WHERE dep.status <> 'done'
        }
        WITH r ORDER BY r.priority DESC, r.created_at ASC LIMIT 1
        SET r.status = 'claimed',
            r.claimed_by = $worker_id,
            r.claimed_at = datetime()
        RETURN r
        """
        ...

    def execute_request(self, request: dict) -> dict:
        """Execute via stack runner and return result."""
        stack_result = create_stack(
            self.conn,
            request['task_id'],
            json.loads(request['parameters']),
            request['request_id']
        )
        run_stack_to_completion(self.conn, stack_result['stack_id'])
        return get_stack_info(self.conn, stack_result['stack_id'])

    def run_loop(self, poll_interval: float = 1.0):
        """Main processing loop."""
        while not self.shutdown_requested:
            request = self.claim_request()
            if request:
                self.mark_executing(request['request_id'])
                try:
                    result = self.execute_request(request)
                    self.mark_done(request['request_id'], result)
                except Exception as e:
                    self.mark_failed(request['request_id'], str(e))
            else:
                time.sleep(poll_interval)
```

**Deployment Options**:
- Standalone daemon: `python -m runner.processor.daemon`
- Systemd service
- Docker container
- Kubernetes deployment

### 4. APOC Triggers

**Trigger 1: Dependency Resolution**

```cypher
CALL apoc.trigger.add(
  'resolve_dependencies',
  '
  UNWIND $committedNodes AS n
  WITH n WHERE n:TaskRequest AND n.status = "done"
  MATCH (waiting:TaskRequest)-[:DEPENDS_ON]->(n)
  WHERE waiting.status = "blocked"
  AND NOT EXISTS {
    MATCH (waiting)-[:DEPENDS_ON]->(other:TaskRequest)
    WHERE other.status <> "done"
  }
  SET waiting.status = "pending"
  ',
  {phase: 'afterAsync'}
);
```

**Trigger 2: Cascade Rules**

```cypher
CALL apoc.trigger.add(
  'cascade_on_source',
  '
  UNWIND $createdNodes AS n
  WITH n WHERE n:Source
  MATCH (rule:CascadeRule {enabled: true})
  WHERE n.kind = rule.source_kind OR rule.source_kind IS NULL
  CREATE (req:TaskRequest {
    request_id: randomUUID(),
    task_id: rule.task_id,
    parameters: apoc.text.replace(rule.parameter_template, "\\$source\\.id", n.source_id),
    status: "pending",
    priority: coalesce(rule.priority, 50),
    requester: "trigger:" + rule.rule_id,
    created_at: datetime()
  })
  CREATE (req)-[:TRIGGERED_BY]->(rule)
  ',
  {phase: 'afterAsync'}
);
```

**Trigger 3: Sync Detection**

```cypher
CALL apoc.trigger.add(
  'mark_sync_pending',
  '
  UNWIND $createdNodes AS n
  WITH n WHERE n:Data AND n.sync_status IS NULL
  SET n.sync_status = "pending"
  ',
  {phase: 'after'}
);
```

### 5. MCP Configuration Update

**File**: `.mcp.json`

```json
{
  "mcpServers": {
    "jsongraph-neo4j-cypher": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--env-file", ".env", "mcp-neo4j-cypher@latest"],
      "env": {
        "NEO4J_DATABASE": "jsongraph",
        "NEO4J_READ_ONLY": "true"
      }
    },
    "hybridgraph-neo4j-cypher": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--env-file", ".env", "mcp-neo4j-cypher@latest"],
      "env": {
        "NEO4J_DATABASE": "hybridgraph",
        "NEO4J_READ_ONLY": "true"
      }
    },
    "runner-mcp": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "runner.mcp.server"],
      "env": {
        "RUNNER_DB": "tasks.db",
        "NEO4J_DATABASE": "hybridgraph"
      }
    }
  }
}
```

---

## Implementation Phases

### Phase 1: Request Schema & Constraints
**Priority**: Critical
**Dependencies**: None

**Deliverables**:
- [ ] Neo4j schema for `:TaskRequest` nodes
- [ ] Constraints and indexes
- [ ] Migration script to add schema to hybridgraph
- [ ] Test queries for CRUD operations

**Files**:
- `src/runner/db/migrations/add_task_requests.py`
- `docs/neo4j-schema.md` (update)

### Phase 2: MCP Server Implementation
**Priority**: Critical
**Dependencies**: Phase 1

**Deliverables**:
- [ ] MCP server skeleton using `mcp` package
- [ ] `submit_task_request` tool
- [ ] `get_request_status` tool
- [ ] `get_task_result` tool
- [ ] `list_available_tasks` tool
- [ ] `cancel_request` tool
- [ ] Unit tests

**Files**:
- `src/runner/mcp/__init__.py`
- `src/runner/mcp/server.py`
- `src/runner/mcp/tools.py`
- `tests/test_mcp/test_server.py`

### Phase 3: Request Processor Daemon
**Priority**: Critical
**Dependencies**: Phase 1, Phase 2

**Deliverables**:
- [ ] Processor daemon with poll loop
- [ ] Atomic claim mechanism (Neo4j transaction)
- [ ] Stack runner integration
- [ ] Status update methods
- [ ] Graceful shutdown handling
- [ ] Logging and metrics
- [ ] Unit and integration tests

**Files**:
- `src/runner/processor/__init__.py`
- `src/runner/processor/daemon.py`
- `src/runner/processor/claims.py`
- `tests/test_processor/test_daemon.py`

### Phase 4: APOC Trigger Configuration
**Priority**: High
**Dependencies**: Phase 1

**Deliverables**:
- [ ] Dependency resolution trigger
- [ ] Cascade rule trigger
- [ ] Sync detection trigger (enhance existing)
- [ ] Cascade rule management API
- [ ] Setup script for triggers
- [ ] Documentation

**Files**:
- `src/runner/triggers/__init__.py`
- `src/runner/triggers/setup.py`
- `src/runner/triggers/cascade_rules.py`
- `scripts/setup_triggers.py`

### Phase 5: Integration & Testing
**Priority**: High
**Dependencies**: Phases 1-4

**Deliverables**:
- [ ] End-to-end integration tests
- [ ] Updated `.mcp.json` configuration
- [ ] Agent workflow documentation
- [ ] Example cascade rules
- [ ] Performance benchmarks
- [ ] Monitoring dashboard queries

**Files**:
- `.mcp.json` (update)
- `tests/test_integration/test_agent_workflow.py`
- `docs/agent-workflow.md`
- `examples/cascade_rules.cypher`

### Phase 6: CLI & Operations
**Priority**: Medium
**Dependencies**: Phases 1-5

**Deliverables**:
- [ ] CLI commands for request management
- [ ] Processor daemon management (start/stop/status)
- [ ] Request queue monitoring
- [ ] Cascade rule management CLI
- [ ] Health check endpoints

**Files**:
- `src/runner/cli.py` (update)
- `docs/operations.md`

---

## File Structure (Final)

```
src/runner/
├── mcp/                          # NEW: MCP server
│   ├── __init__.py
│   ├── server.py                 # Main MCP server
│   └── tools.py                  # Tool implementations
│
├── processor/                    # NEW: Request processor
│   ├── __init__.py
│   ├── daemon.py                 # Main processing loop
│   └── claims.py                 # Atomic claim logic
│
├── triggers/                     # NEW: APOC trigger management
│   ├── __init__.py
│   ├── setup.py                  # Trigger installation
│   └── cascade_rules.py          # Rule CRUD operations
│
├── core/                         # EXISTING: Task execution
│   ├── stack_runner.py           # (unchanged)
│   ├── runner.py                 # (unchanged)
│   └── bootstrap.py              # (minor updates)
│
├── hybridgraph/                  # EXISTING: Graph operations
│   ├── queries.py                # (add request queries)
│   └── ...
│
├── db/
│   └── migrations/
│       ├── add_task_requests.py  # NEW: Request schema
│       └── ...
│
└── cli.py                        # UPDATE: Add processor commands
```

---

## Security Considerations

### Agent Access Control

| Resource | Agent Access | Notes |
|----------|--------------|-------|
| jsongraph | Read-only | Existing MCP server |
| hybridgraph | Read-only | Existing MCP server |
| :TaskRequest nodes | Write via MCP | Controlled by runner-mcp |
| SQLite tasks.db | None | Only processor accesses |
| Stack execution | None | Only processor executes |

### Request Validation

The MCP server validates all requests:
- `task_id` must exist in tasks table
- `parameters` must be valid JSON
- `priority` must be within allowed range (1-1000)
- `request_id` must be unique (for idempotency)
- Rate limiting per requester

### Audit Trail

Every request is logged:
- Creator identity (agent ID, user, system)
- Creation timestamp
- All status transitions with timestamps
- Execution logs linked via `result_ref`
- Error messages preserved

---

## Monitoring & Observability

### Key Metrics

| Metric | Query | Alert Threshold |
|--------|-------|-----------------|
| Pending requests | `MATCH (r:TaskRequest {status:'pending'}) RETURN count(r)` | > 100 |
| Avg processing time | `MATCH (r:TaskRequest {status:'done'}) RETURN avg(duration.between(r.created_at, r.finished_at))` | > 5 min |
| Failed requests (24h) | `MATCH (r:TaskRequest {status:'failed'}) WHERE r.finished_at > datetime() - duration('P1D') RETURN count(r)` | > 10 |
| Processor heartbeat | Custom metric from daemon | Missing for > 60s |

### Dashboard Queries

```cypher
// Request status distribution
MATCH (r:TaskRequest)
RETURN r.status, count(r) as count
ORDER BY count DESC;

// Recent failures with errors
MATCH (r:TaskRequest {status: 'failed'})
RETURN r.request_id, r.task_id, r.error, r.finished_at
ORDER BY r.finished_at DESC
LIMIT 10;

// Active cascade rules
MATCH (rule:CascadeRule {enabled: true})
OPTIONAL MATCH (req:TaskRequest)-[:TRIGGERED_BY]->(rule)
RETURN rule.rule_id, rule.task_id, count(req) as triggered_count;

// Processing throughput (last hour)
MATCH (r:TaskRequest {status: 'done'})
WHERE r.finished_at > datetime() - duration('PT1H')
RETURN count(r) as completed_last_hour;
```

---

## Example Workflows

### Workflow 1: Agent Submits Data Processing Request

```
1. Agent queries hybridgraph for context
2. Agent calls runner-mcp.submit_task_request(
     task_id="batch_upload_dual",
     parameters={"json_paths": ["file1.json", "file2.json"]},
     priority=100
   )
3. MCP server creates :TaskRequest node
4. Processor claims and executes
5. Results written to hybridgraph
6. Agent polls get_request_status() until done
7. Agent retrieves results via get_task_result()
```

### Workflow 2: Cascade-Triggered Processing

```
1. upload_dual task writes new :Source node
2. APOC trigger matches :CascadeRule {task_id: "validate_schema"}
3. New :TaskRequest created automatically
4. Processor executes validation
5. If validation fails, another cascade creates alert task
6. System continues without human intervention
```

### Workflow 3: Dependent Task Chain

```
1. Agent submits request A (convert CSV to JSON)
2. Agent submits request B (upload to graph) with DEPENDS_ON A
3. Request B starts with status="blocked"
4. Processor completes A, marks done
5. APOC trigger resolves dependency, B becomes pending
6. Processor executes B
7. Agent only needs to poll B's final status
```

---

## Success Criteria

### Phase 1 Complete When:
- [ ] `:TaskRequest` schema deployed to hybridgraph
- [ ] All constraints and indexes created
- [ ] Can manually create/query requests via Cypher

### Phase 2 Complete When:
- [ ] MCP server starts and responds to tool calls
- [ ] Can submit requests from Claude Code
- [ ] Can query request status
- [ ] All tools have unit tests passing

### Phase 3 Complete When:
- [ ] Processor daemon runs as background service
- [ ] Processes requests end-to-end
- [ ] Handles failures gracefully
- [ ] Logs all operations

### Phase 4 Complete When:
- [ ] All APOC triggers installed
- [ ] Dependency resolution works automatically
- [ ] Cascade rules can be created/modified
- [ ] At least one example cascade rule active

### Phase 5 Complete When:
- [ ] Full workflow tested end-to-end
- [ ] Agent can submit → process → retrieve results
- [ ] Cascade triggers work in production
- [ ] Documentation complete

### Phase 6 Complete When:
- [ ] CLI commands for all operations
- [ ] Monitoring queries documented
- [ ] Operations runbook complete

---

## Appendix A: Comparison with agent-table-cycle

| Feature | agent-table-cycle | Our Implementation |
|---------|-------------------|-------------------|
| Agent execution | Claude SDK, no tools | MCP tools (controlled) |
| Request storage | Neo4j :Task nodes | Neo4j :TaskRequest nodes |
| Execution | TypeScript runner | Python stack_runner |
| Results | Neo4j decomposition | jsongraph/hybridgraph |
| Triggers | Conditional run_if/run_unless | APOC triggers + cascade rules |
| Deduplication | dedupe_key field | request_id + Merkle hashes |

**Key Differences**:
- We keep the proven stack_runner execution model
- We use APOC triggers instead of in-code condition evaluation
- We maintain dual-database architecture (jsongraph + hybridgraph)
- We add MCP for agent access rather than custom SDK integration

---

## Appendix B: Migration Path

For existing installations:

1. **Backup**: Export existing hybridgraph data
2. **Schema**: Run migration to add :TaskRequest schema
3. **Triggers**: Install APOC triggers (non-destructive)
4. **MCP**: Update .mcp.json with runner-mcp
5. **Processor**: Deploy daemon (can run alongside existing stack_runner CLI)
6. **Test**: Verify with simple request submission
7. **Migrate**: Gradually move manual CLI usage to MCP submission

No breaking changes to existing functionality. All new components are additive.
