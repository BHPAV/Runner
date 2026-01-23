#!/usr/bin/env python3
"""
Stack Runner - LIFO execution with monadic context accumulation.

Unlike the queue runner (FIFO), this runner:
- Executes tasks in LIFO order (most recent first)
- Passes accumulated context between tasks
- Allows tasks to push new tasks onto the stack
- Builds a complete execution trace

Usage:
    python stack_runner.py start <task_id> [--params '{}']
    python stack_runner.py resume <stack_id>
    python stack_runner.py run-one <stack_id>

Environment Variables:
    TASK_DB             SQLite database path (default: ./tasks.db)
    RUNS_DIR            Output directory for JSON logs (default: ./runs)
    TASK_LEASE_SECONDS  Lease duration (default: 300)
"""

import argparse
import json
import os
import platform
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


# =============================================================================
# Configuration
# =============================================================================

def get_config() -> dict:
    return {
        "db_path": os.environ.get("TASK_DB", "./tasks.db"),
        "runs_dir": os.environ.get("RUNS_DIR", "./runs"),
        "lease_seconds": int(os.environ.get("TASK_LEASE_SECONDS", "300")),
    }


def get_worker_id() -> str:
    hostname = platform.node() or "unknown"
    pid = os.getpid()
    return f"{hostname}:{pid}"


# =============================================================================
# Utilities
# =============================================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_json(text: str, default: Any = None) -> Any:
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def merge_dicts(*dicts) -> dict:
    """Merge multiple dicts, later ones override earlier."""
    result = {}
    for d in dicts:
        if d:
            result.update(d)
    return result


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class StackContext:
    """
    The monadic state that flows through execution.
    Tasks can read from and contribute to this context.
    """
    variables: dict = field(default_factory=dict)    # Named values
    outputs: list = field(default_factory=list)       # All task outputs
    decisions: list = field(default_factory=list)     # Audit trail
    errors: list = field(default_factory=list)        # Any errors encountered
    metadata: dict = field(default_factory=dict)      # Arbitrary metadata

    def bind(self, task_output: dict) -> 'StackContext':
        """Monadic bind - incorporate task output into context."""
        new_vars = merge_dicts(self.variables, task_output.get("variables", {}))
        new_outputs = [*self.outputs, task_output.get("output")]
        new_decisions = [*self.decisions, *task_output.get("decisions", [])]
        new_errors = [*self.errors, *task_output.get("errors", [])]
        new_metadata = merge_dicts(self.metadata, task_output.get("metadata", {}))

        return StackContext(
            variables=new_vars,
            outputs=new_outputs,
            decisions=new_decisions,
            errors=new_errors,
            metadata=new_metadata,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'StackContext':
        return cls(**data) if data else cls()


@dataclass
class PushedTask:
    """A task to be pushed onto the stack."""
    task_id: str
    parameters: dict = field(default_factory=dict)
    reason: str = ""  # Why this task was pushed


@dataclass
class TaskResult:
    """Result returned by task execution."""
    output: Any = None                              # Direct output value
    variables: dict = field(default_factory=dict)   # Variables to add to context
    decisions: list = field(default_factory=list)   # Decisions made
    errors: list = field(default_factory=list)      # Errors encountered
    metadata: dict = field(default_factory=dict)    # Additional metadata
    push_tasks: list = field(default_factory=list)  # Tasks to push onto stack
    abort: bool = False                             # Abort entire stack on error


@dataclass
class TraceEntry:
    """Single entry in the execution trace."""
    queue_id: int
    request_id: str
    task_id: str
    depth: int
    started_at: str
    finished_at: str
    status: str
    input_context: dict
    output: Any
    output_context: dict
    pushed_tasks: list
    execution_ms: int
    error: Optional[str] = None


@dataclass
class CostMetrics:
    wall_ms: int = 0
    cpu_user_ms: int = 0
    cpu_sys_ms: int = 0
    max_rss_kb: int = 0


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    cost: CostMetrics
    started_at: str
    finished_at: str
    timed_out: bool = False
    parsed_result: Optional[TaskResult] = None


# =============================================================================
# Database Operations
# =============================================================================

def init_stack_schema(conn: sqlite3.Connection, schema_path: str = "./schema_stack.sql") -> None:
    """Initialize stack tables."""
    schema_file = Path(schema_path)
    if schema_file.exists():
        conn.executescript(schema_file.read_text())
        conn.commit()


def create_stack(
    conn: sqlite3.Connection,
    task_id: str,
    parameters: dict = None,
    request_id: str = None,
) -> dict:
    """Create a new execution stack and queue the initial task."""
    stack_id = str(uuid.uuid4())
    request_id = request_id or str(uuid.uuid4())
    parameters = parameters or {}
    now = utc_now()

    # Create the stack
    conn.execute(
        """
        INSERT INTO execution_stacks
        (stack_id, created_at, status, initial_request_id, initial_task_id, initial_params_json)
        VALUES (?, ?, 'running', ?, ?, ?)
        """,
        (stack_id, now, request_id, task_id, json.dumps(parameters))
    )

    # Queue the initial task
    cur = conn.execute(
        """
        INSERT INTO stack_queue
        (request_id, stack_id, task_id, depth, sequence, status, enqueued_at, parameters_json, input_context_json)
        VALUES (?, ?, ?, 0, 0, 'queued', ?, ?, '{}')
        """,
        (request_id, stack_id, task_id, now, json.dumps(parameters))
    )
    queue_id = cur.lastrowid
    conn.commit()

    return {
        "stack_id": stack_id,
        "queue_id": queue_id,
        "request_id": request_id,
        "task_id": task_id,
    }


def acquire_stack_task(
    conn: sqlite3.Connection,
    stack_id: str,
    worker_id: str,
    lease_seconds: int
) -> Optional[dict]:
    """
    LIFO acquisition - get the highest queue_id (most recently added) queued task.
    Uses DYNAMIC context: task gets current stack context at execution time,
    not the context from when it was pushed. This enables true monadic composition.
    """
    now = utc_now()
    lease_dt = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    lease_expires = lease_dt.isoformat(timespec="milliseconds")

    # LIFO: ORDER BY queue_id DESC (newest first)
    cur = conn.execute(
        """
        UPDATE stack_queue
        SET status = 'running',
            worker_id = ?,
            lease_expires_at = ?,
            started_at = ?
        WHERE queue_id = (
            SELECT queue_id FROM stack_queue
            WHERE stack_id = ?
              AND (status = 'queued' OR (status = 'running' AND lease_expires_at < ?))
            ORDER BY queue_id DESC
            LIMIT 1
        )
        RETURNING queue_id, request_id, task_id, depth, parent_queue_id,
                  parameters_json, enqueued_at
        """,
        (worker_id, lease_expires, now, stack_id, now)
    )

    row = cur.fetchone()
    conn.commit()

    if row:
        # Get CURRENT stack context (dynamic, not static from push time)
        current_context = get_stack_context(conn, stack_id)

        # Update the task's input_context to reflect what it actually received
        conn.execute(
            "UPDATE stack_queue SET input_context_json = ? WHERE queue_id = ?",
            (json.dumps(current_context.to_dict()), row["queue_id"])
        )
        conn.commit()

        return {
            "queue_id": row["queue_id"],
            "request_id": row["request_id"],
            "task_id": row["task_id"],
            "depth": row["depth"],
            "parent_queue_id": row["parent_queue_id"],
            "parameters": load_json(row["parameters_json"], {}),
            "input_context": current_context,  # Dynamic context!
            "enqueued_at": row["enqueued_at"],
        }
    return None


def push_tasks_to_stack(
    conn: sqlite3.Connection,
    stack_id: str,
    parent_queue_id: int,
    parent_depth: int,
    tasks: list[PushedTask],
    context: StackContext,
) -> list[dict]:
    """Push new tasks onto the stack. Returns info about pushed tasks."""
    pushed_info = []
    now = utc_now()
    context_json = json.dumps(context.to_dict())

    # Push in reverse order so they execute in the order specified
    # (since LIFO will pop the last one first)
    for seq, task in enumerate(reversed(tasks)):
        request_id = str(uuid.uuid4())
        cur = conn.execute(
            """
            INSERT INTO stack_queue
            (request_id, stack_id, task_id, depth, parent_queue_id, sequence,
             status, enqueued_at, parameters_json, input_context_json)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
            """,
            (request_id, stack_id, task.task_id, parent_depth + 1, parent_queue_id,
             seq, now, json.dumps(task.parameters), context_json)
        )
        pushed_info.append({
            "queue_id": cur.lastrowid,
            "request_id": request_id,
            "task_id": task.task_id,
            "parameters": task.parameters,
            "reason": task.reason,
        })

    conn.commit()
    return list(reversed(pushed_info))  # Return in original order


def finalize_stack_task(
    conn: sqlite3.Connection,
    queue_id: int,
    status: str,
    output: Any,
    output_context: StackContext,
    pushed_tasks: list[dict],
    error: str = None,
) -> None:
    """Mark a stack task as complete."""
    now = utc_now()
    conn.execute(
        """
        UPDATE stack_queue
        SET status = ?,
            finished_at = ?,
            output_json = ?,
            output_context_json = ?,
            pushed_tasks_json = ?,
            error_message = ?,
            worker_id = NULL,
            lease_expires_at = NULL
        WHERE queue_id = ?
        """,
        (status, now, json.dumps(output), json.dumps(output_context.to_dict()),
         json.dumps(pushed_tasks), error, queue_id)
    )
    conn.commit()


def update_stack_context(conn: sqlite3.Connection, stack_id: str, context: StackContext) -> None:
    """Update the stack's accumulated context."""
    conn.execute(
        "UPDATE execution_stacks SET context_json = ? WHERE stack_id = ?",
        (json.dumps(context.to_dict()), stack_id)
    )
    conn.commit()


def get_stack_context(conn: sqlite3.Connection, stack_id: str) -> StackContext:
    """Get the current accumulated context for a stack."""
    cur = conn.execute(
        "SELECT context_json FROM execution_stacks WHERE stack_id = ?",
        (stack_id,)
    )
    row = cur.fetchone()
    if row:
        return StackContext.from_dict(load_json(row["context_json"], {}))
    return StackContext()


def check_stack_complete(conn: sqlite3.Connection, stack_id: str) -> bool:
    """Check if all tasks in the stack are complete."""
    cur = conn.execute(
        "SELECT COUNT(*) FROM stack_queue WHERE stack_id = ? AND status IN ('queued', 'running')",
        (stack_id,)
    )
    return cur.fetchone()[0] == 0


def finalize_stack(
    conn: sqlite3.Connection,
    stack_id: str,
    status: str,
    final_output: Any = None,
    error: str = None,
) -> None:
    """Mark an entire stack as complete."""
    now = utc_now()

    # Build the trace from all completed tasks
    cur = conn.execute(
        """
        SELECT queue_id, request_id, task_id, depth, status, started_at, finished_at,
               input_context_json, output_json, output_context_json, pushed_tasks_json, error_message
        FROM stack_queue
        WHERE stack_id = ?
        ORDER BY queue_id
        """,
        (stack_id,)
    )

    trace = []
    for row in cur.fetchall():
        started = row["started_at"] or ""
        finished = row["finished_at"] or ""
        exec_ms = 0
        if started and finished:
            try:
                start_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(finished.replace('Z', '+00:00'))
                exec_ms = int((end_dt - start_dt).total_seconds() * 1000)
            except:
                pass

        trace.append({
            "queue_id": row["queue_id"],
            "request_id": row["request_id"],
            "task_id": row["task_id"],
            "depth": row["depth"],
            "status": row["status"],
            "started_at": started,
            "finished_at": finished,
            "execution_ms": exec_ms,
            "input_context": load_json(row["input_context_json"], {}),
            "output": load_json(row["output_json"]),
            "output_context": load_json(row["output_context_json"], {}),
            "pushed_tasks": load_json(row["pushed_tasks_json"], []),
            "error": row["error_message"],
        })

    conn.execute(
        """
        UPDATE execution_stacks
        SET status = ?, finished_at = ?, trace_json = ?, final_output_json = ?, error_message = ?
        WHERE stack_id = ?
        """,
        (status, now, json.dumps(trace), json.dumps(final_output), error, stack_id)
    )
    conn.commit()


def get_stack_info(conn: sqlite3.Connection, stack_id: str) -> Optional[dict]:
    """Get stack information."""
    cur = conn.execute(
        """
        SELECT stack_id, created_at, finished_at, status, initial_task_id,
               context_json, trace_json, final_output_json, error_message
        FROM execution_stacks
        WHERE stack_id = ?
        """,
        (stack_id,)
    )
    row = cur.fetchone()
    if row:
        return {
            "stack_id": row["stack_id"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "initial_task_id": row["initial_task_id"],
            "context": load_json(row["context_json"], {}),
            "trace": load_json(row["trace_json"], []),
            "final_output": load_json(row["final_output_json"]),
            "error": row["error_message"],
        }
    return None


# =============================================================================
# Task Definition Fetch
# =============================================================================

def fetch_task_definition(conn: sqlite3.Connection, task_id: str) -> Optional[dict]:
    """Fetch task definition from tasks table."""
    cur = conn.execute(
        """
        SELECT task_id, task_type, code, parameters_json, working_dir,
               env_json, timeout_seconds, enabled
        FROM tasks
        WHERE task_id = ?
        """,
        (task_id,)
    )
    row = cur.fetchone()

    if not row:
        return None

    return {
        "task_id": row["task_id"],
        "task_type": row["task_type"],
        "code": row["code"],
        "parameters": load_json(row["parameters_json"], {}),
        "working_dir": row["working_dir"],
        "env": load_json(row["env_json"], {}),
        "timeout_seconds": row["timeout_seconds"] or 300,
        "enabled": bool(row["enabled"]),
    }


# =============================================================================
# Task Execution
# =============================================================================

def execute_task(
    task_type: str,
    code: str,
    params: dict,
    context: StackContext,
    working_dir: Optional[str],
    env_vars: dict,
    timeout_seconds: int,
    queue_id: int,
    stack_id: str,
    db_path: str,
) -> ExecutionResult:
    """Execute a task with context available."""
    started_at = utc_now()

    # Build environment - include context for task to read
    exec_env = os.environ.copy()
    exec_env.update(env_vars)
    exec_env["TASK_PARAMS"] = json.dumps(params)
    exec_env["TASK_CONTEXT"] = json.dumps(context.to_dict())
    exec_env["TASK_QUEUE_ID"] = str(queue_id)
    exec_env["TASK_STACK_ID"] = stack_id
    exec_env["TASK_DB"] = db_path

    cwd = working_dir if working_dir else None

    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall_start = time.monotonic()

    timed_out = False
    exit_code = 0
    stdout_data = ""
    stderr_data = ""

    try:
        if task_type == "cli":
            formatted_code = code.format(**params)
            result = subprocess.run(
                formatted_code,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=cwd,
                env=exec_env,
            )

        elif task_type == "python":
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                script_path = f.name

            try:
                result = subprocess.run(
                    [sys.executable, script_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    cwd=cwd,
                    env=exec_env,
                )
            finally:
                os.unlink(script_path)

        elif task_type == "typescript":
            with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
                f.write(code)
                script_path = f.name

            try:
                result = subprocess.run(
                    ["npx", "ts-node", script_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    cwd=cwd,
                    env=exec_env,
                )
            finally:
                os.unlink(script_path)

        else:
            raise ValueError(f"Unknown task type: {task_type}")

        exit_code = result.returncode
        stdout_data = result.stdout
        stderr_data = result.stderr

    except subprocess.TimeoutExpired as e:
        timed_out = True
        exit_code = -1
        stdout_data = e.stdout.decode() if e.stdout else ""
        stderr_data = e.stderr.decode() if e.stderr else ""
        stderr_data += f"\n[TIMEOUT after {timeout_seconds}s]"

    except Exception as e:
        exit_code = -2
        stderr_data = f"Execution error: {str(e)}"

    wall_end = time.monotonic()
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)

    cost = CostMetrics(
        wall_ms=int((wall_end - wall_start) * 1000),
        cpu_user_ms=int((usage_after.ru_utime - usage_before.ru_utime) * 1000),
        cpu_sys_ms=int((usage_after.ru_stime - usage_before.ru_stime) * 1000),
        max_rss_kb=usage_after.ru_maxrss if sys.platform == "linux" else usage_after.ru_maxrss // 1024,
    )

    finished_at = utc_now()

    # Try to parse structured result from stdout
    parsed_result = None
    if exit_code == 0:
        parsed_result = parse_task_result(stdout_data)

    return ExecutionResult(
        exit_code=exit_code,
        stdout=stdout_data,
        stderr=stderr_data,
        cost=cost,
        started_at=started_at,
        finished_at=finished_at,
        timed_out=timed_out,
        parsed_result=parsed_result,
    )


def parse_task_result(stdout: str) -> Optional[TaskResult]:
    """
    Parse structured result from task stdout.

    Tasks can output JSON with special structure:
    {
        "__task_result__": true,
        "output": <any>,
        "variables": {},
        "decisions": [],
        "push_tasks": [{"task_id": "...", "parameters": {}, "reason": "..."}],
        "abort": false
    }
    """
    # Look for JSON block in output
    lines = stdout.strip().split('\n')

    # Try to find a JSON result block (last JSON object in output)
    for line in reversed(lines):
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                data = json.loads(line)
                if data.get("__task_result__"):
                    push_tasks = []
                    for pt in data.get("push_tasks", []):
                        push_tasks.append(PushedTask(
                            task_id=pt["task_id"],
                            parameters=pt.get("parameters", {}),
                            reason=pt.get("reason", ""),
                        ))

                    return TaskResult(
                        output=data.get("output"),
                        variables=data.get("variables", {}),
                        decisions=data.get("decisions", []),
                        errors=data.get("errors", []),
                        metadata=data.get("metadata", {}),
                        push_tasks=push_tasks,
                        abort=data.get("abort", False),
                    )
            except json.JSONDecodeError:
                continue

    # No structured result - treat stdout as plain output
    return TaskResult(output=stdout.strip() if stdout.strip() else None)


# =============================================================================
# Main Runner Logic
# =============================================================================

def run_stack_step(
    conn: sqlite3.Connection,
    stack_id: str,
    config: dict,
    verbose: bool = False,
) -> tuple[bool, str]:
    """
    Execute one step of the stack.

    Returns: (continue_running, status_message)
    """
    worker_id = get_worker_id()
    db_path = config["db_path"]
    lease_seconds = config["lease_seconds"]

    # Check if stack is still running
    stack_info = get_stack_info(conn, stack_id)
    if not stack_info:
        return False, "Stack not found"

    if stack_info["status"] != "running":
        return False, f"Stack already {stack_info['status']}"

    # Acquire next task (LIFO)
    task_info = acquire_stack_task(conn, stack_id, worker_id, lease_seconds)
    if not task_info:
        # No more tasks - stack is complete
        context = get_stack_context(conn, stack_id)
        finalize_stack(conn, stack_id, "done", final_output=context.to_dict())
        return False, "Stack complete"

    queue_id = task_info["queue_id"]
    task_id = task_info["task_id"]
    depth = task_info["depth"]
    params = task_info["parameters"]
    input_context = task_info["input_context"]

    if verbose:
        print(f"  [{depth}] Executing: {task_id} (queue_id={queue_id})")

    # Fetch task definition
    task_def = fetch_task_definition(conn, task_id)
    if not task_def:
        finalize_stack_task(conn, queue_id, "failed", None, input_context, [], f"Task not found: {task_id}")
        return True, f"Task not found: {task_id}"

    if not task_def["enabled"]:
        finalize_stack_task(conn, queue_id, "cancelled", None, input_context, [], "Task disabled")
        return True, f"Task disabled: {task_id}"

    # Merge parameters
    merged_params = merge_dicts(task_def["parameters"], params)

    # Execute
    exec_result = execute_task(
        task_type=task_def["task_type"],
        code=task_def["code"],
        params=merged_params,
        context=input_context,
        working_dir=task_def["working_dir"],
        env_vars=task_def["env"],
        timeout_seconds=task_def["timeout_seconds"],
        queue_id=queue_id,
        stack_id=stack_id,
        db_path=db_path,
    )

    # Process result
    task_result = exec_result.parsed_result or TaskResult()

    if exec_result.exit_code != 0:
        task_result.errors.append(f"Exit code: {exec_result.exit_code}")
        if exec_result.stderr:
            task_result.errors.append(exec_result.stderr)

    # Compute output context (bind the result to input context)
    output_context = input_context.bind({
        "output": task_result.output,
        "variables": task_result.variables,
        "decisions": task_result.decisions,
        "errors": task_result.errors,
        "metadata": task_result.metadata,
    })

    # Push any new tasks
    pushed_info = []
    if task_result.push_tasks and exec_result.exit_code == 0:
        pushed_info = push_tasks_to_stack(
            conn, stack_id, queue_id, depth, task_result.push_tasks, output_context
        )
        if verbose:
            for pi in pushed_info:
                print(f"    → Pushed: {pi['task_id']} ({pi['reason']})")

    # Determine status
    if task_result.abort:
        status = "failed"
        finalize_stack_task(conn, queue_id, status, task_result.output, output_context, pushed_info, "Task requested abort")
        finalize_stack(conn, stack_id, "failed", error="Task requested abort")
        return False, "Stack aborted by task"
    elif exec_result.exit_code != 0:
        status = "failed"
    else:
        status = "done"

    finalize_stack_task(conn, queue_id, status, task_result.output, output_context, pushed_info)

    # Update stack's accumulated context
    update_stack_context(conn, stack_id, output_context)

    if verbose:
        print(f"      Status: {status}, Wall: {exec_result.cost.wall_ms}ms")
        if task_result.output:
            out_str = str(task_result.output)[:100]
            print(f"      Output: {out_str}{'...' if len(str(task_result.output)) > 100 else ''}")

    return True, f"Completed {task_id}"


def run_stack_to_completion(
    conn: sqlite3.Connection,
    stack_id: str,
    config: dict,
    verbose: bool = False,
) -> dict:
    """Run all tasks in a stack until completion."""
    if verbose:
        print(f"Running stack: {stack_id}")

    step = 0
    while True:
        step += 1
        if verbose:
            print(f"Step {step}:")

        continue_running, message = run_stack_step(conn, stack_id, config, verbose)

        if not continue_running:
            if verbose:
                print(f"  → {message}")
            break

    return get_stack_info(conn, stack_id)


def save_stack_output(runs_dir: str, stack_id: str, stack_info: dict) -> str:
    """Save stack output to JSON file."""
    Path(runs_dir).mkdir(parents=True, exist_ok=True)

    filename = f"stack_{stack_id[:8]}.json"
    filepath = Path(runs_dir) / filename

    output = {
        "stack_id": stack_info["stack_id"],
        "status": stack_info["status"],
        "created_at": stack_info["created_at"],
        "finished_at": stack_info["finished_at"],
        "initial_task_id": stack_info["initial_task_id"],
        "final_context": stack_info["context"],
        "final_output": stack_info["final_output"],
        "trace": stack_info["trace"],
        "error": stack_info["error"],
    }

    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)

    return str(filepath)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Stack Runner - LIFO execution with monadic context")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start a new execution stack")
    start_parser.add_argument("task_id", help="Initial task to execute")
    start_parser.add_argument("--params", default="{}", help="JSON parameters")
    start_parser.add_argument("--request-id", help="Idempotency key")

    # Resume command
    resume_parser = subparsers.add_parser("resume", help="Resume an existing stack")
    resume_parser.add_argument("stack_id", help="Stack ID to resume")

    # Run-one command
    runone_parser = subparsers.add_parser("run-one", help="Run one step of a stack")
    runone_parser.add_argument("stack_id", help="Stack ID")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check stack status")
    status_parser.add_argument("stack_id", help="Stack ID")

    args = parser.parse_args()
    config = get_config()

    # Ensure runs directory exists
    Path(config["runs_dir"]).mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(config["db_path"])
    conn.row_factory = sqlite3.Row

    # Initialize stack schema
    script_dir = Path(__file__).parent
    init_stack_schema(conn, str(script_dir / "schema_stack.sql"))

    try:
        if args.command == "start":
            params = load_json(args.params, {})
            result = create_stack(conn, args.task_id, params, args.request_id)

            if args.verbose:
                print(f"Created stack: {result['stack_id']}")
                print(f"Initial task: {result['task_id']} (queue_id={result['queue_id']})")

            # Run to completion
            stack_info = run_stack_to_completion(conn, result["stack_id"], config, args.verbose)

            output_path = save_stack_output(config["runs_dir"], result["stack_id"], stack_info)

            print(f"\nStack {stack_info['status']}: {result['stack_id']}")
            print(f"Output saved to: {output_path}")

            sys.exit(0 if stack_info["status"] == "done" else 2)

        elif args.command == "resume":
            stack_info = run_stack_to_completion(conn, args.stack_id, config, args.verbose)
            output_path = save_stack_output(config["runs_dir"], args.stack_id, stack_info)

            print(f"\nStack {stack_info['status']}: {args.stack_id}")
            print(f"Output saved to: {output_path}")

            sys.exit(0 if stack_info["status"] == "done" else 2)

        elif args.command == "run-one":
            continue_running, message = run_stack_step(conn, args.stack_id, config, args.verbose)
            print(message)
            sys.exit(0 if continue_running else 1)

        elif args.command == "status":
            stack_info = get_stack_info(conn, args.stack_id)
            if stack_info:
                print(json.dumps(stack_info, indent=2))
            else:
                print(f"Stack not found: {args.stack_id}")
                sys.exit(1)

        else:
            parser.print_help()
            sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
