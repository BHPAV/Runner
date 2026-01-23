-- Stack Runner SQLite Schema Extension
-- LIFO stack execution with monadic context accumulation

-- Execution stacks group related tasks
CREATE TABLE IF NOT EXISTS execution_stacks (
    stack_id           TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL DEFAULT 'running',  -- running|done|failed|cancelled
    initial_request_id TEXT NOT NULL,
    initial_task_id    TEXT NOT NULL,
    initial_params_json TEXT DEFAULT '{}',
    context_json       TEXT DEFAULT '{}',      -- Accumulated context (the monad state)
    trace_json         TEXT DEFAULT '[]',      -- Full execution trace
    final_output_json  TEXT,                   -- Final computed output
    error_message      TEXT
);

-- Stack queue - tasks within an execution stack
CREATE TABLE IF NOT EXISTS stack_queue (
    queue_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id         TEXT NOT NULL UNIQUE,
    stack_id           TEXT NOT NULL,
    task_id            TEXT NOT NULL,
    depth              INTEGER NOT NULL DEFAULT 0,
    parent_queue_id    INTEGER,
    sequence           INTEGER NOT NULL DEFAULT 0,  -- Order among siblings
    status             TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed|cancelled
    enqueued_at        TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT,
    worker_id          TEXT,
    lease_expires_at   TEXT,
    parameters_json    TEXT DEFAULT '{}',
    -- Execution context and results:
    input_context_json TEXT DEFAULT '{}',     -- Context when task started
    output_json        TEXT,                   -- Task's direct output
    output_context_json TEXT,                  -- Context after task completed
    pushed_tasks_json  TEXT DEFAULT '[]',      -- Tasks this task pushed onto stack
    error_message      TEXT,
    FOREIGN KEY(stack_id) REFERENCES execution_stacks(stack_id),
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(parent_queue_id) REFERENCES stack_queue(queue_id)
);

-- Indexes for stack operations
CREATE INDEX IF NOT EXISTS idx_stack_status ON execution_stacks(status);
CREATE INDEX IF NOT EXISTS idx_stack_queue_stack ON stack_queue(stack_id);
CREATE INDEX IF NOT EXISTS idx_stack_queue_status ON stack_queue(status);
CREATE INDEX IF NOT EXISTS idx_stack_queue_request ON stack_queue(request_id);
CREATE INDEX IF NOT EXISTS idx_stack_queue_depth ON stack_queue(stack_id, depth);
