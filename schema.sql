-- Task Runner SQLite Schema
-- Supports multi-worker locking, cost accounting, kill-switch, and task fan-out

-- Core task definitions
CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    task_type          TEXT NOT NULL,        -- 'cli' | 'python' | 'typescript'
    code               TEXT NOT NULL,        -- script body or command template
    parameters_json    TEXT NOT NULL DEFAULT '{}',
    working_dir        TEXT,
    env_json           TEXT DEFAULT '{}',
    timeout_seconds    INTEGER DEFAULT 300,
    enabled            INTEGER DEFAULT 1
);

-- Task queue with lease support
CREATE TABLE IF NOT EXISTS task_queue (
    queue_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id         TEXT NOT NULL UNIQUE,     -- UUID for idempotency
    task_id            TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed|cancelled
    enqueued_at        TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT,
    worker_id          TEXT,
    lease_expires_at   TEXT,
    parameters_json    TEXT DEFAULT '{}',  -- Override parameters for this specific run
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Global control flags (kill switch)
CREATE TABLE IF NOT EXISTS control_flags (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Task fan-out tracking (dual mode: existing tasks OR inline tasks)
CREATE TABLE IF NOT EXISTS task_fanout (
    fanout_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_queue_id       INTEGER NOT NULL,
    -- For existing tasks:
    child_task_id         TEXT,
    child_parameters_json TEXT DEFAULT '{}',
    -- For inline tasks:
    inline_task_type      TEXT,              -- 'cli' | 'python' | 'typescript'
    inline_code           TEXT,
    inline_timeout        INTEGER DEFAULT 300,
    -- Metadata:
    created_at            TEXT NOT NULL,
    processed             INTEGER DEFAULT 0,
    FOREIGN KEY(parent_queue_id) REFERENCES task_queue(queue_id),
    CHECK (child_task_id IS NOT NULL OR inline_code IS NOT NULL)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_queue_status ON task_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_lease ON task_queue(lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_queue_request_id ON task_queue(request_id);
CREATE INDEX IF NOT EXISTS idx_fanout_parent ON task_fanout(parent_queue_id);
CREATE INDEX IF NOT EXISTS idx_fanout_processed ON task_fanout(processed);
