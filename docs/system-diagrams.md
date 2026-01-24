# System Interaction Diagrams

This document provides detailed diagrams showing how the Runner system components interact with Neo4j graph databases.

## Table of Contents

- [High-Level Architecture](#high-level-architecture)
- [Graph Database Structure](#graph-database-structure)
- [Data Flow Diagrams](#data-flow-diagrams)
- [Node Relationship Maps](#node-relationship-maps)
- [Sequence Diagrams](#sequence-diagrams)

---

## High-Level Architecture

### Complete System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    AGENT LAYER                                          │
│                                                                                         │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │                         Claude / AI Agent                                        │  │
│   │                                                                                  │  │
│   │   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐             │  │
│   │   │ jsongraph-mcp    │  │ hybridgraph-mcp  │  │   runner-mcp     │             │  │
│   │   │ (read-only)      │  │ (read-only)      │  │ (task submit)    │             │  │
│   │   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘             │  │
│   └────────────┼─────────────────────┼─────────────────────┼────────────────────────┘  │
│                │                     │                     │                           │
└────────────────┼─────────────────────┼─────────────────────┼───────────────────────────┘
                 │                     │                     │
                 │ Cypher (read)       │ Cypher (read)       │ Create :TaskRequest
                 ▼                     ▼                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   NEO4J CLUSTER                                         │
│                                                                                         │
│   ┌─────────────────────────┐         ┌─────────────────────────────────────────────┐  │
│   │      jsongraph          │         │              hybridgraph                     │  │
│   │                         │         │                                              │  │
│   │  ┌─────────────────┐   │         │  ┌─────────────────┐  ┌─────────────────┐   │  │
│   │  │ :Data nodes     │   │  sync   │  │ :Source         │  │ :TaskRequest    │   │  │
│   │  │ :JsonDoc        │───┼────────▶│  │ :Structure      │  │ :CascadeRule    │   │  │
│   │  │ :JsonNode       │   │         │  │ :Content        │  │ :SchemaVersion  │   │  │
│   │  └─────────────────┘   │         │  └─────────────────┘  └────────┬────────┘   │  │
│   │                         │         │                               │             │  │
│   └─────────────────────────┘         └───────────────────────────────┼─────────────┘  │
│                                                                       │                 │
│   ┌───────────────────────────────────────────────────────────────────┼─────────────┐  │
│   │                         APOC TRIGGERS                             │             │  │
│   │   • resolve_dependencies (unblock waiting requests)               │             │  │
│   │   • cascade_on_source (create requests from rules)      ◀─────────┘             │  │
│   │   • mark_sync_pending (flag new data for sync)                                  │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          │ Poll :TaskRequest {status: 'pending'}
                                          ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               EXECUTION LAYER                                           │
│                                                                                         │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │                        Request Processor Daemon                                  │  │
│   │                                                                                  │  │
│   │   1. Poll Neo4j for pending :TaskRequest                                        │  │
│   │   2. Claim atomically (status → 'claimed')                                      │  │
│   │   3. Create stack in SQLite                                                      │  │
│   │   4. Execute via Stack Runner                                                    │  │
│   │   5. Update :TaskRequest (status → 'done', result_ref)                          │  │
│   │                                                                                  │  │
│   └──────────────────────────────────┬──────────────────────────────────────────────┘  │
│                                      │                                                  │
│                                      ▼                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │                           Stack Runner (LIFO)                                    │  │
│   │                                                                                  │  │
│   │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │  │
│   │   │ tasks.db    │───▶│ Task Queue  │───▶│ Execute     │───▶│ runs/*.json │     │  │
│   │   │ (SQLite)    │    │ (LIFO)      │    │ Subprocess  │    │ (outputs)   │     │  │
│   │   └─────────────┘    └─────────────┘    └──────┬──────┘    └─────────────┘     │  │
│   │                                                │                                │  │
│   └────────────────────────────────────────────────┼────────────────────────────────┘  │
│                                                    │                                    │
│                                                    │ Write :Source, :Data nodes        │
│                                                    ▼                                    │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │                           Upload Tasks                                           │  │
│   │                                                                                  │  │
│   │   • upload_jsongraph  → writes to jsongraph only                                │  │
│   │   • upload_dual       → writes to both jsongraph AND hybridgraph                │  │
│   │   • batch_upload_dual → bulk operations                                          │  │
│   │                                                                                  │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Graph Database Structure

### jsongraph Database Schema

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           jsongraph                                      │
│                                                                          │
│   FLAT STORAGE MODEL - Denormalized for fast writes                     │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                         :Data Node                               │   │
│   │                                                                  │   │
│   │   Properties:                                                    │   │
│   │   • id: string (unique identifier)                              │   │
│   │   • source_file: string                                          │   │
│   │   • content: string (JSON or raw content)                       │   │
│   │   • metadata: map                                                │   │
│   │   • created_at: datetime                                         │   │
│   │   • sync_status: string ('pending' | 'synced')                  │   │
│   │                                                                  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                           │
│                              │ :HAS_DOCUMENT                            │
│                              ▼                                           │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                       :JsonDoc Node                              │   │
│   │                                                                  │   │
│   │   Properties:                                                    │   │
│   │   • doc_id: string                                               │   │
│   │   • root_path: string ("$")                                     │   │
│   │                                                                  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                           │
│                              │ :HAS_CHILD                               │
│                              ▼                                           │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                      :JsonNode Node                              │   │
│   │                                                                  │   │
│   │   Properties:                                                    │   │
│   │   • path: string (JSONPath, e.g., "$.users[0].name")            │   │
│   │   • kind: string ('object' | 'array' | 'value')                 │   │
│   │   • key: string (property name or array index)                  │   │
│   │   • value_str / value_num / value_bool                          │   │
│   │                                                                  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                           │
│                              │ :HAS_CHILD (recursive)                   │
│                              ▼                                           │
│                            [...]                                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### hybridgraph Database Schema

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          hybridgraph                                     │
│                                                                          │
│   DEDUPLICATED MODEL - Content-addressable with Merkle hashes           │
│   ~90% smaller than jsongraph for duplicate-heavy data                  │
│                                                                          │
│   ┌────────────────────────────────────────────────────────────────┐    │
│   │                        :Source Node                             │    │
│   │                                                                 │    │
│   │   Properties:                                                   │    │
│   │   • source_id: string (unique identifier)                      │    │
│   │   • kind: string ('json' | 'csv' | 'xml' | etc.)              │    │
│   │   • origin: string (file path or URL)                          │    │
│   │   • created_at: datetime                                        │    │
│   │   • root_hash: string (Merkle hash of root structure)          │    │
│   │                                                                 │    │
│   │   TRIGGERS: cascade_on_source fires when created               │    │
│   │                                                                 │    │
│   └───────────────────────────┬────────────────────────────────────┘    │
│                               │                                          │
│                               │ :HAS_ROOT                               │
│                               ▼                                          │
│   ┌────────────────────────────────────────────────────────────────┐    │
│   │                      :Structure Node                            │    │
│   │                                                                 │    │
│   │   Properties:                                                   │    │
│   │   • hash: string (Merkle hash: "m:" + SHA256[:32])             │    │
│   │   • kind: string ('object' | 'array')                          │    │
│   │   • key: string                                                 │    │
│   │   • ref_count: integer (number of parents referencing)         │    │
│   │                                                                 │    │
│   │   DEDUPLICATION: Same structure = same hash = one node         │    │
│   │                                                                 │    │
│   └───────────────────────────┬────────────────────────────────────┘    │
│                               │                                          │
│               ┌───────────────┼───────────────┐                         │
│               │               │               │                         │
│               ▼               ▼               ▼                         │
│          :HAS_CHILD      :HAS_CHILD      :HAS_CHILD                    │
│               │               │               │                         │
│               ▼               ▼               ▼                         │
│   ┌───────────────┐  ┌───────────────┐  ┌────────────────────────┐    │
│   │  :Structure   │  │  :Structure   │  │      :Content          │    │
│   │  (nested obj) │  │  (nested arr) │  │                        │    │
│   └───────────────┘  └───────────────┘  │   Properties:          │    │
│                                          │   • hash: string       │    │
│                                          │     ("c:" + SHA256)    │    │
│                                          │   • kind: string       │    │
│                                          │   • key: string        │    │
│                                          │   • value_str          │    │
│                                          │   • value_num          │    │
│                                          │   • value_bool         │    │
│                                          │   • ref_count: int     │    │
│                                          │                        │    │
│                                          │   LEAF NODE            │    │
│                                          └────────────────────────┘    │
│                                                                          │
│   ════════════════════════════════════════════════════════════════════  │
│                                                                          │
│   ┌────────────────────────────────────────────────────────────────┐    │
│   │                     :TaskRequest Node                           │    │
│   │                                                                 │    │
│   │   Properties:                                                   │    │
│   │   • request_id: string (UUID, unique)                          │    │
│   │   • task_id: string                                             │    │
│   │   • parameters: string (JSON)                                   │    │
│   │   • status: string                                              │    │
│   │   • priority: integer (1-1000)                                  │    │
│   │   • requester: string                                           │    │
│   │   • created_at / claimed_at / finished_at: datetime            │    │
│   │   • claimed_by: string (worker ID)                              │    │
│   │   • result_ref: string (link to output)                        │    │
│   │   • error: string                                               │    │
│   │                                                                 │    │
│   └───────────────────────────┬────────────────────────────────────┘    │
│                               │                                          │
│           ┌───────────────────┼───────────────────┐                     │
│           │                   │                   │                     │
│           ▼                   ▼                   ▼                     │
│      :DEPENDS_ON        :TRIGGERED_BY        :PRODUCED                  │
│           │                   │                   │                     │
│           ▼                   ▼                   ▼                     │
│   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐              │
│   │ :TaskRequest  │  │ :CascadeRule  │  │   :Source     │              │
│   │ (dependency)  │  │               │  │   (output)    │              │
│   └───────────────┘  └───────────────┘  └───────────────┘              │
│                                                                          │
│   ┌────────────────────────────────────────────────────────────────┐    │
│   │                      :CascadeRule Node                          │    │
│   │                                                                 │    │
│   │   Properties:                                                   │    │
│   │   • rule_id: string (unique)                                    │    │
│   │   • description: string                                         │    │
│   │   • source_kind: string (filter, null = all)                   │    │
│   │   • task_id: string                                             │    │
│   │   • parameter_template: string (JSON with $source.* vars)      │    │
│   │   • priority: integer                                           │    │
│   │   • enabled: boolean                                            │    │
│   │   • created_at: datetime                                        │    │
│   │                                                                 │    │
│   └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow Diagrams

### Flow 1: Agent Submits Task Request

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   AGENT                                                                  │
│     │                                                                    │
│     │  1. submit_task_request(task_id="upload_dual", params={...})     │
│     │                                                                    │
│     ▼                                                                    │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                      runner-mcp Server                           │   │
│   │                                                                  │   │
│   │  2. Validate task_id exists in tasks.db                         │   │
│   │  3. Generate request_id (UUID)                                   │   │
│   │  4. Check for idempotent duplicate                               │   │
│   │                                                                  │   │
│   └───────────────────────────┬─────────────────────────────────────┘   │
│                               │                                          │
│                               │  5. CREATE (:TaskRequest {...})          │
│                               ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    hybridgraph Database                          │   │
│   │                                                                  │   │
│   │   (:TaskRequest {                                                │   │
│   │       request_id: "abc-123",                                     │   │
│   │       task_id: "upload_dual",                                    │   │
│   │       parameters: '{"json_path": "data.json"}',                 │   │
│   │       status: "pending",                                         │   │
│   │       priority: 100,                                             │   │
│   │       requester: "mcp:user",                                     │   │
│   │       created_at: datetime()                                     │   │
│   │   })                                                             │   │
│   │                                                                  │   │
│   └───────────────────────────┬─────────────────────────────────────┘   │
│                               │                                          │
│                               │  6. Return to agent                      │
│                               ▼                                          │
│   AGENT receives:                                                        │
│   {                                                                      │
│       "request_id": "abc-123",                                          │
│       "status": "pending",                                               │
│       "is_new": true                                                     │
│   }                                                                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Flow 2: Processor Executes Request

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   REQUEST PROCESSOR (Daemon Loop)                                        │
│     │                                                                    │
│     │  1. Poll for pending requests                                     │
│     │     MATCH (r:TaskRequest {status: 'pending'})                     │
│     │     WHERE no unmet dependencies                                    │
│     │     ORDER BY priority DESC, created_at ASC                        │
│     │     LIMIT 1                                                        │
│     │                                                                    │
│     ▼                                                                    │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │  2. ATOMIC CLAIM                                                 │   │
│   │                                                                  │   │
│   │     SET r.status = 'claimed',                                    │   │
│   │         r.claimed_by = 'hostname:12345',                         │   │
│   │         r.claimed_at = datetime()                                │   │
│   │                                                                  │   │
│   └───────────────────────────┬─────────────────────────────────────┘   │
│                               │                                          │
│                               │  3. Update to 'executing'                │
│                               │     SET r.status = 'executing'           │
│                               ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │  4. STACK RUNNER EXECUTION                                       │   │
│   │                                                                  │   │
│   │     ┌─────────────┐                                              │   │
│   │     │  tasks.db   │  create_stack(task_id, params, request_id)  │   │
│   │     └──────┬──────┘                                              │   │
│   │            │                                                     │   │
│   │            ▼                                                     │   │
│   │     ┌─────────────┐                                              │   │
│   │     │ Stack Queue │  LIFO execution                              │   │
│   │     │   (SQLite)  │                                              │   │
│   │     └──────┬──────┘                                              │   │
│   │            │                                                     │   │
│   │            ▼                                                     │   │
│   │     ┌─────────────┐                                              │   │
│   │     │  Subprocess │  Execute task code (Python/CLI/TS)          │   │
│   │     └──────┬──────┘                                              │   │
│   │            │                                                     │   │
│   │            ├──────────────────────────────────────┐              │   │
│   │            │                                      │              │   │
│   │            ▼                                      ▼              │   │
│   │     ┌─────────────┐                       ┌─────────────┐       │   │
│   │     │  jsongraph  │                       │ hybridgraph │       │   │
│   │     │  :Data      │     (if upload_dual)  │  :Source    │       │   │
│   │     │  :JsonDoc   │                       │  :Structure │       │   │
│   │     │  :JsonNode  │                       │  :Content   │       │   │
│   │     └─────────────┘                       └─────────────┘       │   │
│   │                                                                  │   │
│   └───────────────────────────┬─────────────────────────────────────┘   │
│                               │                                          │
│                               │  5. Save output                          │
│                               ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │     runs/stack_abc123.json                                       │   │
│   │     {                                                            │   │
│   │         "stack_id": "...",                                       │   │
│   │         "status": "done",                                        │   │
│   │         "final_output": {...},                                   │   │
│   │         "trace": [...]                                           │   │
│   │     }                                                            │   │
│   └───────────────────────────┬─────────────────────────────────────┘   │
│                               │                                          │
│                               │  6. Update TaskRequest                   │
│                               ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │     SET r.status = 'done',                                       │   │
│   │         r.finished_at = datetime(),                              │   │
│   │         r.result_ref = 'stack_abc123'                            │   │
│   │                                                                  │   │
│   │     ─────── APOC TRIGGER FIRES ───────                          │   │
│   │     resolve_dependencies: unblocks waiting requests              │   │
│   │                                                                  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Flow 3: Cascade Rule Triggers New Request

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   TASK WRITES NEW :Source NODE                                           │
│     │                                                                    │
│     │  upload_dual task creates:                                        │
│     │  CREATE (:Source {source_id: "data_001", kind: "json", ...})     │
│     │                                                                    │
│     ▼                                                                    │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    APOC TRIGGER: cascade_on_source               │   │
│   │                                                                  │   │
│   │  UNWIND $createdNodes AS n                                       │   │
│   │  WITH n WHERE n:Source                                           │   │
│   │  MATCH (rule:CascadeRule {enabled: true})                        │   │
│   │  WHERE rule.source_kind IS NULL OR n.kind = rule.source_kind    │   │
│   │                                                                  │   │
│   └───────────────────────────┬─────────────────────────────────────┘   │
│                               │                                          │
│                               │  Finds matching rule:                    │
│                               │  (:CascadeRule {                         │
│                               │      rule_id: "validate_json",           │
│                               │      source_kind: "json",                │
│                               │      task_id: "validate_json",           │
│                               │      parameter_template:                 │
│                               │        '{"source_id":"$source.source_id"}'│
│                               │  })                                      │
│                               ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │  TRIGGER CREATES NEW :TaskRequest                                │   │
│   │                                                                  │   │
│   │  CREATE (req:TaskRequest {                                       │   │
│   │      request_id: randomUUID(),                                   │   │
│   │      task_id: "validate_json",                                   │   │
│   │      parameters: '{"source_id": "data_001"}',  ◀── substituted  │   │
│   │      status: "pending",                                          │   │
│   │      priority: 50,                                               │   │
│   │      requester: "trigger:validate_json",                         │   │
│   │      created_at: datetime()                                      │   │
│   │  })                                                              │   │
│   │  CREATE (req)-[:TRIGGERED_BY]->(rule)                            │   │
│   │                                                                  │   │
│   └───────────────────────────┬─────────────────────────────────────┘   │
│                               │                                          │
│                               │  New request now in queue                │
│                               ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │              PROCESSOR PICKS UP NEW REQUEST                      │   │
│   │                                                                  │   │
│   │  (Cycle continues - request gets executed, may create more      │   │
│   │   :Source nodes, triggering more cascade rules...)              │   │
│   │                                                                  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Node Relationship Maps

### Complete Node Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│                              HYBRIDGRAPH RELATIONSHIPS                                   │
│                                                                                          │
│                                                                                          │
│     ┌──────────────┐                              ┌──────────────┐                      │
│     │:TaskRequest  │─────:DEPENDS_ON─────────────▶│:TaskRequest  │                      │
│     │              │                              │ (dependency) │                      │
│     │ request_id   │                              └──────────────┘                      │
│     │ task_id      │                                                                     │
│     │ status       │◀─────:TRIGGERED_BY──────────┐                                      │
│     │ priority     │                              │                                      │
│     │ ...          │                              │                                      │
│     └──────┬───────┘                      ┌──────┴───────┐                              │
│            │                              │:CascadeRule  │                              │
│            │                              │              │                              │
│            │:PRODUCED                     │ rule_id      │                              │
│            │                              │ task_id      │                              │
│            ▼                              │ source_kind  │                              │
│     ┌──────────────┐                      │ enabled      │                              │
│     │   :Source    │                      └──────────────┘                              │
│     │              │                                                                     │
│     │ source_id    │                                                                     │
│     │ kind         │                                                                     │
│     │ origin       │                                                                     │
│     │ root_hash    │                                                                     │
│     └──────┬───────┘                                                                     │
│            │                                                                             │
│            │:HAS_ROOT                                                                    │
│            ▼                                                                             │
│     ┌──────────────┐                                                                     │
│     │ :Structure   │◀───────────────────────────────────────────┐                       │
│     │              │                                             │                       │
│     │ hash (m:...) │                                             │                       │
│     │ kind         │                                             │                       │
│     │ key          │                                             │                       │
│     │ ref_count    │                                             │ (multiple sources     │
│     └──────┬───────┘                                             │  can share same       │
│            │                                                     │  structure via hash)  │
│            │:HAS_CHILD (with key or idx property)               │                       │
│            │                                                     │                       │
│     ┌──────┴──────────────────────┬──────────────────────┐      │                       │
│     │                             │                      │      │                       │
│     ▼                             ▼                      ▼      │                       │
│  ┌──────────────┐          ┌──────────────┐      ┌──────────────┐                       │
│  │ :Structure   │          │ :Structure   │      │  :Content    │                       │
│  │ (nested obj) │          │ (nested arr) │      │              │                       │
│  │              │          │              │      │ hash (c:...) │                       │
│  │ ref_count:2  │──────────┼──────────────┼──────│ kind         │                       │
│  │              │          │              │      │ key          │                       │
│  └──────────────┘          └──────────────┘      │ value_*      │                       │
│         ▲                                        │ ref_count:5  │◀──(shared by 5 nodes) │
│         │                                        └──────────────┘                       │
│         │                                                                                │
│         └─── Another :Source with same structure ────────────────────────────────────┘  │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│                               JSONGRAPH RELATIONSHIPS                                    │
│                                                                                          │
│     ┌──────────────┐                                                                     │
│     │    :Data     │                                                                     │
│     │              │                                                                     │
│     │ id           │                                                                     │
│     │ source_file  │                                                                     │
│     │ sync_status  │ ◀── 'pending' triggers sync to hybridgraph                        │
│     └──────┬───────┘                                                                     │
│            │                                                                             │
│            │:HAS_DOCUMENT                                                                │
│            ▼                                                                             │
│     ┌──────────────┐                                                                     │
│     │  :JsonDoc    │                                                                     │
│     │              │                                                                     │
│     │ doc_id       │                                                                     │
│     │ root_path    │                                                                     │
│     └──────┬───────┘                                                                     │
│            │                                                                             │
│            │:HAS_CHILD                                                                   │
│            ▼                                                                             │
│     ┌──────────────┐                                                                     │
│     │  :JsonNode   │◀────────────┐                                                      │
│     │              │             │                                                       │
│     │ path         │             │:HAS_CHILD                                            │
│     │ kind         │             │                                                       │
│     │ key          │─────────────┘ (recursive tree structure)                           │
│     │ value_*      │                                                                     │
│     └──────────────┘                                                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Sequence Diagrams

### Complete Request Lifecycle

```
┌───────┐     ┌───────────┐     ┌───────────┐     ┌───────────┐     ┌──────────┐     ┌───────────┐
│ Agent │     │runner-mcp │     │hybridgraph│     │ Processor │     │StackRun  │     │ Databases │
└───┬───┘     └─────┬─────┘     └─────┬─────┘     └─────┬─────┘     └────┬─────┘     └─────┬─────┘
    │               │                 │                 │                │                 │
    │ submit_task   │                 │                 │                │                 │
    │──────────────▶│                 │                 │                │                 │
    │               │                 │                 │                │                 │
    │               │ CREATE          │                 │                │                 │
    │               │ :TaskRequest    │                 │                │                 │
    │               │────────────────▶│                 │                │                 │
    │               │                 │                 │                │                 │
    │               │◀────────────────│                 │                │                 │
    │               │  request_id     │                 │                │                 │
    │◀──────────────│                 │                 │                │                 │
    │  {request_id} │                 │                 │                │                 │
    │               │                 │                 │                │                 │
    │               │                 │   poll          │                │                 │
    │               │                 │◀────────────────│                │                 │
    │               │                 │                 │                │                 │
    │               │                 │   claim         │                │                 │
    │               │                 │◀────────────────│                │                 │
    │               │                 │   (atomic SET)  │                │                 │
    │               │                 │                 │                │                 │
    │               │                 │                 │ create_stack   │                 │
    │               │                 │                 │───────────────▶│                 │
    │               │                 │                 │                │                 │
    │               │                 │                 │                │ execute task    │
    │               │                 │                 │                │────────────────▶│
    │               │                 │                 │                │                 │
    │               │                 │                 │                │   write nodes   │
    │               │                 │◀────────────────┼────────────────┼─────────────────│
    │               │                 │   (if upload)   │                │                 │
    │               │                 │                 │                │                 │
    │               │                 │                 │◀───────────────│                 │
    │               │                 │                 │  stack result  │                 │
    │               │                 │                 │                │                 │
    │               │                 │   update status │                │                 │
    │               │                 │◀────────────────│                │                 │
    │               │                 │   status='done' │                │                 │
    │               │                 │                 │                │                 │
    │               │                 │ ═══════════════════════════════════════════════   │
    │               │                 │ ║ APOC TRIGGER: resolve_dependencies            ║  │
    │               │                 │ ║ APOC TRIGGER: cascade_on_source (if :Source) ║  │
    │               │                 │ ═══════════════════════════════════════════════   │
    │               │                 │                 │                │                 │
    │ get_status    │                 │                 │                │                 │
    │──────────────▶│                 │                 │                │                 │
    │               │ MATCH           │                 │                │                 │
    │               │────────────────▶│                 │                │                 │
    │               │◀────────────────│                 │                │                 │
    │◀──────────────│                 │                 │                │                 │
    │ {status:done} │                 │                 │                │                 │
    │               │                 │                 │                │                 │
    │ get_result    │                 │                 │                │                 │
    │──────────────▶│                 │                 │                │                 │
    │               │ read result_ref │                 │                │                 │
    │               │─────────────────────────────────────────────────────────────────────▶│
    │               │◀────────────────────────────────────────────────────────────────────│
    │◀──────────────│                 │                 │                │                 │
    │ {output:...}  │                 │                 │                │                 │
    │               │                 │                 │                │                 │
```

---

## Summary

The system creates a **continuous processing loop**:

```
Agent submits → TaskRequest created → Processor executes → Results written
                      ▲                                          │
                      │                                          │
                      └──── Cascade rules create new requests ◀──┘
```

Key integration points:

| Component | Reads From | Writes To |
|-----------|-----------|-----------|
| Agent (MCP) | jsongraph, hybridgraph | hybridgraph (:TaskRequest) |
| Processor | hybridgraph (:TaskRequest) | hybridgraph (:TaskRequest status) |
| Stack Runner | tasks.db | jsongraph, hybridgraph, runs/ |
| APOC Triggers | hybridgraph events | hybridgraph (:TaskRequest) |
