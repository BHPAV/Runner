#!/usr/bin/env python3
"""
Task Runner - Single-file executor with multi-worker locking, cost accounting,
kill-switch, and task fan-out support.

Usage:
    python runner.py [--once] [--verbose]

Environment Variables:
    TASK_DB             SQLite database path (default: ./tasks.db)
    RUNS_DIR            Output directory for JSON logs (default: ./runs)
    TASK_LEASE_SECONDS  Lease duration before task can be stolen (default: 300)

Exit Codes:
    0 - Task completed successfully
    1 - No task available
    2 - Error occurred
    3 - Kill switch is active
"""

import argparse
import json
import os
import platform
import resource
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# =============================================================================
# Configuration
# =============================================================================

def get_config() -> dict:
    """Load configuration from environment variables."""
    return {
        "db_path": os.environ.get("TASK_DB", "./tasks.db"),
        "runs_dir": os.environ.get("RUNS_DIR", "./runs"),
        "lease_seconds": int(os.environ.get("TASK_LEASE_SECONDS", "300")),
    }


def get_worker_id() -> str:
    """Generate unique worker identifier: hostname:pid."""
    hostname = platform.node() or "unknown"
    pid = os.getpid()
    return f"{hostname}:{pid}"


# =============================================================================
# Utility Helpers
# =============================================================================

def utc_now() -> str:
    """Return current UTC time in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_json(text: str, default: Any = None) -> Any:
    """Safely parse JSON string, return default on failure."""
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def merge_params(base: dict, override: dict) -> dict:
    """Merge parameter dictionaries, with override taking precedence."""
    result = dict(base)
    result.update(override)
    return result


# =============================================================================
# Data Classes for JSON Output
# =============================================================================

@dataclass
class CostMetrics:
    wall_ms: int = 0
    cpu_user_ms: int = 0
    cpu_sys_ms: int = 0
    max_rss_kb: int = 0


@dataclass
class Ref:
    ref: str
    role: str
    channel: str
    content: str


@dataclass
class Action:
    action_id: str
    kind: str
    started_at: str
    finished_at: str
    exit_code: int
    cost: CostMetrics
    refs: list = field(default_factory=list)


@dataclass
class WorkerInfo:
    host: str
    pid: int


@dataclass
class TaskDefinition:
    """Complete task definition for output JSON."""
    task_id: str
    task_type: str
    code: str
    parameters: dict
    working_dir: Optional[str]
    env: dict
    timeout_seconds: int
    enabled: bool


@dataclass
class QueueEntry:
    """Queue entry details for output JSON."""
    queue_id: int
    request_id: str
    task_id: str
    enqueued_at: str
    queue_parameters: dict


@dataclass
class RunRecord:
    run_id: str
    queue: QueueEntry
    task: TaskDefinition
    worker: WorkerInfo
    started_at: str
    finished_at: str
    status: str
    merged_parameters: dict


@dataclass
class RunOutput:
    run: RunRecord
    actions: list
    fanout: list


# =============================================================================
# Kill Switch Check
# =============================================================================

def check_kill_switch(conn: sqlite3.Connection) -> bool:
    """Check if global kill switch is active. Returns True if should stop."""
    cur = conn.execute(
        "SELECT value FROM control_flags WHERE key = 'kill_all'"
    )
    row = cur.fetchone()
    if row and row["value"] == "1":
        return True
    return False


def check_pause_flag(conn: sqlite3.Connection) -> bool:
    """Check if new task processing is paused."""
    cur = conn.execute(
        "SELECT value FROM control_flags WHERE key = 'pause_new_tasks'"
    )
    row = cur.fetchone()
    if row and row["value"] == "1":
        return True
    return False


# =============================================================================
# Lease Acquisition (Multi-Worker Safe)
# =============================================================================

def acquire_task(conn: sqlite3.Connection, worker_id: str, lease_seconds: int) -> Optional[dict]:
    """
    Atomically claim a queued task OR steal an expired lease.
    Uses compare-and-swap UPDATE for safe multi-worker operation.
    Returns task info dict or None if no task available.
    """
    now = utc_now()
    lease_expires = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    # Calculate lease expiry
    from datetime import timedelta
    lease_dt = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    lease_expires = lease_dt.isoformat(timespec="milliseconds")

    # Atomically claim a queued task or steal an expired lease
    # ORDER BY queue_id ensures FIFO (first-in-first-out)
    cur = conn.execute(
        """
        UPDATE task_queue
        SET status = 'running',
            worker_id = ?,
            lease_expires_at = ?,
            started_at = ?
        WHERE queue_id = (
            SELECT queue_id FROM task_queue
            WHERE status = 'queued'
               OR (status = 'running' AND lease_expires_at < ?)
            ORDER BY queue_id
            LIMIT 1
        )
        RETURNING queue_id, request_id, task_id, parameters_json, enqueued_at
        """,
        (worker_id, lease_expires, now, now)
    )

    row = cur.fetchone()
    conn.commit()

    if row:
        return {
            "queue_id": row["queue_id"],
            "request_id": row["request_id"],
            "task_id": row["task_id"],
            "enqueued_at": row["enqueued_at"],
            "queue_parameters": load_json(row["parameters_json"], {}),
        }
    return None


def check_task_cancelled(conn: sqlite3.Connection, queue_id: int) -> bool:
    """Check if a specific task has been cancelled."""
    cur = conn.execute(
        "SELECT status FROM task_queue WHERE queue_id = ?",
        (queue_id,)
    )
    row = cur.fetchone()
    return row and row["status"] == "cancelled"


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

@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    cost: CostMetrics
    started_at: str
    finished_at: str
    timed_out: bool = False


def execute_task(
    task_type: str,
    code: str,
    params: dict,
    working_dir: Optional[str],
    env_vars: dict,
    timeout_seconds: int,
    queue_id: int,
    db_path: str,
) -> ExecutionResult:
    """
    Execute a task based on its type.
    Returns ExecutionResult with output and metrics.
    """
    started_at = utc_now()

    # Build environment
    exec_env = os.environ.copy()
    exec_env.update(env_vars)
    exec_env["TASK_PARAMS"] = json.dumps(params)
    exec_env["TASK_QUEUE_ID"] = str(queue_id)
    exec_env["TASK_DB"] = db_path

    # Resolve working directory
    cwd = working_dir if working_dir else None

    # Get resource usage before
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall_start = time.monotonic()

    timed_out = False
    exit_code = 0
    stdout_data = ""
    stderr_data = ""

    try:
        if task_type == "cli":
            # Format code with parameters for CLI tasks
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
            # Write code to temp file and execute
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False
            ) as f:
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
            # Write code to temp file and execute with ts-node
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".ts", delete=False
            ) as f:
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

        elif task_type == "python_file":
            # code contains the filename relative to working directory or absolute
            script_path = code
            if not os.path.isabs(script_path):
                # Relative to the runner's directory
                script_path = os.path.join(os.path.dirname(__file__), script_path)

            if not os.path.exists(script_path):
                raise ValueError(f"Python file not found: {script_path}")

            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=cwd,
                env=exec_env,
            )

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

    # Calculate cost metrics
    wall_end = time.monotonic()
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)

    cost = CostMetrics(
        wall_ms=int((wall_end - wall_start) * 1000),
        cpu_user_ms=int((usage_after.ru_utime - usage_before.ru_utime) * 1000),
        cpu_sys_ms=int((usage_after.ru_stime - usage_before.ru_stime) * 1000),
        max_rss_kb=usage_after.ru_maxrss if sys.platform == "linux" else usage_after.ru_maxrss // 1024,
    )

    finished_at = utc_now()

    return ExecutionResult(
        exit_code=exit_code,
        stdout=stdout_data,
        stderr=stderr_data,
        cost=cost,
        started_at=started_at,
        finished_at=finished_at,
        timed_out=timed_out,
    )


# =============================================================================
# Fan-Out Processing
# =============================================================================

def process_fanout(conn: sqlite3.Connection, queue_id: int) -> list:
    """
    Process fan-out records for a completed task.
    Supports both existing task references and inline tasks.
    Returns list of created queue entries for JSON output.
    """
    fanout_records = []

    cur = conn.execute(
        """
        SELECT fanout_id, child_task_id, child_parameters_json,
               inline_task_type, inline_code, inline_timeout
        FROM task_fanout
        WHERE parent_queue_id = ? AND processed = 0
        """,
        (queue_id,)
    )

    rows = cur.fetchall()

    for row in rows:
        fanout_id = row["fanout_id"]
        child_task_id = row["child_task_id"]
        child_params = load_json(row["child_parameters_json"], {})
        inline_type = row["inline_task_type"]
        inline_code = row["inline_code"]
        inline_timeout = row["inline_timeout"] or 300

        new_queue_id = None

        if child_task_id:
            # Mode 1: Queue an existing task
            child_request_id = str(uuid.uuid4())
            cur2 = conn.execute(
                """
                INSERT INTO task_queue (request_id, task_id, status, enqueued_at, parameters_json)
                VALUES (?, ?, 'queued', ?, ?)
                """,
                (child_request_id, child_task_id, utc_now(), json.dumps(child_params))
            )
            new_queue_id = cur2.lastrowid

            fanout_records.append({
                "fanout_id": fanout_id,
                "mode": "existing_task",
                "child_task_id": child_task_id,
                "child_queue_id": new_queue_id,
                "child_request_id": child_request_id,
                "parameters": child_params,
            })

        elif inline_code:
            # Mode 2: Create and queue an inline task
            ephemeral_task_id = f"inline_{queue_id}_{fanout_id}_{uuid.uuid4().hex[:8]}"
            child_request_id = str(uuid.uuid4())

            # Insert ephemeral task definition
            conn.execute(
                """
                INSERT INTO tasks (task_id, task_type, code, parameters_json, timeout_seconds, enabled)
                VALUES (?, ?, ?, '{}', ?, 1)
                """,
                (ephemeral_task_id, inline_type, inline_code, inline_timeout)
            )

            # Queue the ephemeral task
            cur2 = conn.execute(
                """
                INSERT INTO task_queue (request_id, task_id, status, enqueued_at, parameters_json)
                VALUES (?, ?, 'queued', ?, ?)
                """,
                (child_request_id, ephemeral_task_id, utc_now(), json.dumps(child_params))
            )
            new_queue_id = cur2.lastrowid

            fanout_records.append({
                "fanout_id": fanout_id,
                "mode": "inline_task",
                "child_task_id": ephemeral_task_id,
                "child_queue_id": new_queue_id,
                "child_request_id": child_request_id,
                "task_type": inline_type,
            })

        # Mark fanout as processed
        conn.execute(
            "UPDATE task_fanout SET processed = 1 WHERE fanout_id = ?",
            (fanout_id,)
        )

    conn.commit()
    return fanout_records


# =============================================================================
# Status Update
# =============================================================================

def finalize_task(
    conn: sqlite3.Connection,
    queue_id: int,
    status: str,
    finished_at: str
) -> None:
    """Update task queue entry with final status."""
    conn.execute(
        """
        UPDATE task_queue
        SET status = ?, finished_at = ?, worker_id = NULL, lease_expires_at = NULL
        WHERE queue_id = ?
        """,
        (status, finished_at, queue_id)
    )
    conn.commit()


# =============================================================================
# JSON Output Generation
# =============================================================================

def generate_run_output(
    run_id: str,
    queue_entry: dict,
    task_def: dict,
    worker_id: str,
    started_at: str,
    finished_at: str,
    status: str,
    exec_result: Optional[ExecutionResult],
    merged_params: dict,
    fanout_records: list,
) -> dict:
    """Generate Run/Action/Ref shaped JSON for Neo4j ingestion."""
    hostname, pid_str = worker_id.split(":", 1)
    pid = int(pid_str)

    queue_info = QueueEntry(
        queue_id=queue_entry["queue_id"],
        request_id=queue_entry["request_id"],
        task_id=queue_entry["task_id"],
        enqueued_at=queue_entry["enqueued_at"],
        queue_parameters=queue_entry["queue_parameters"],
    )

    task_info = TaskDefinition(
        task_id=task_def["task_id"],
        task_type=task_def["task_type"],
        code=task_def["code"],
        parameters=task_def["parameters"],
        working_dir=task_def["working_dir"],
        env=task_def["env"],
        timeout_seconds=task_def["timeout_seconds"],
        enabled=task_def["enabled"],
    )

    run_record = RunRecord(
        run_id=run_id,
        queue=queue_info,
        task=task_info,
        worker=WorkerInfo(host=hostname, pid=pid),
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        merged_parameters=merged_params,
    )

    actions = []
    if exec_result:
        refs = [
            Ref(ref="stdout", role="output", channel="text", content=exec_result.stdout),
            Ref(ref="stderr", role="output", channel="text", content=exec_result.stderr),
        ]

        action = Action(
            action_id=str(uuid.uuid4()),
            kind=task_def["task_type"],
            started_at=exec_result.started_at,
            finished_at=exec_result.finished_at,
            exit_code=exec_result.exit_code,
            cost=exec_result.cost,
            refs=refs,
        )
        actions.append(action)

    output = RunOutput(
        run=run_record,
        actions=actions,
        fanout=fanout_records,
    )

    # Convert to dict with proper serialization
    def to_dict(obj):
        if hasattr(obj, "__dataclass_fields__"):
            return {k: to_dict(v) for k, v in asdict(obj).items()}
        elif isinstance(obj, list):
            return [to_dict(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: to_dict(v) for k, v in obj.items()}
        return obj

    return to_dict(output)


def save_run_output(runs_dir: str, task_id: str, run_id: str, output: dict) -> str:
    """Save run output to JSON file. Returns the file path."""
    Path(runs_dir).mkdir(parents=True, exist_ok=True)

    # Sanitize task_id for filename
    safe_task_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
    filename = f"run_{safe_task_id}_{run_id[:8]}.json"
    filepath = Path(runs_dir) / filename

    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)

    return str(filepath)


# =============================================================================
# Main Runner Loop
# =============================================================================

def run_once(config: dict, verbose: bool = False) -> int:
    """
    Execute a single task from the queue.

    Returns:
        0 - Task completed successfully
        1 - No task available
        2 - Error occurred
        3 - Kill switch is active
    """
    worker_id = get_worker_id()
    db_path = config["db_path"]
    runs_dir = config["runs_dir"]
    lease_seconds = config["lease_seconds"]

    if verbose:
        print(f"Worker: {worker_id}")
        print(f"Database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Check kill switch
        if check_kill_switch(conn):
            if verbose:
                print("Kill switch is active. Exiting.")
            return 3

        # Check pause flag
        if check_pause_flag(conn):
            if verbose:
                print("Task processing is paused. Exiting.")
            return 1

        # Acquire task
        task_info = acquire_task(conn, worker_id, lease_seconds)
        if not task_info:
            if verbose:
                print("No tasks available.")
            return 1

        queue_id = task_info["queue_id"]
        task_id = task_info["task_id"]
        queue_params = task_info["queue_parameters"]

        if verbose:
            print(f"Acquired task: {task_id} (queue_id={queue_id})")

        # Generate run ID
        run_id = str(uuid.uuid4())
        run_started_at = utc_now()

        # Fetch task definition
        task_def = fetch_task_definition(conn, task_id)
        if not task_def:
            if verbose:
                print(f"Task definition not found: {task_id}")
            finalize_task(conn, queue_id, "failed", utc_now())
            return 2

        if not task_def["enabled"]:
            if verbose:
                print(f"Task is disabled: {task_id}")
            finalize_task(conn, queue_id, "cancelled", utc_now())
            return 2

        # Merge parameters (queue overrides task defaults)
        params = merge_params(task_def["parameters"], queue_params)

        if verbose:
            print(f"Task type: {task_def['task_type']}")
            print(f"Timeout: {task_def['timeout_seconds']}s")
            print(f"Parameters: {json.dumps(params)}")

        # Execute task
        exec_result = execute_task(
            task_type=task_def["task_type"],
            code=task_def["code"],
            params=params,
            working_dir=task_def["working_dir"],
            env_vars=task_def["env"],
            timeout_seconds=task_def["timeout_seconds"],
            queue_id=queue_id,
            db_path=db_path,
        )

        # Check if cancelled during execution
        if check_task_cancelled(conn, queue_id):
            if verbose:
                print("Task was cancelled during execution.")
            finalize_task(conn, queue_id, "cancelled", utc_now())
            status = "cancelled"
        elif exec_result.exit_code == 0:
            status = "done"
        else:
            status = "failed"

        run_finished_at = utc_now()

        # Process fan-out (only if task succeeded)
        fanout_records = []
        if status == "done":
            fanout_records = process_fanout(conn, queue_id)
            if verbose and fanout_records:
                print(f"Created {len(fanout_records)} fan-out tasks")

        # Finalize task
        finalize_task(conn, queue_id, status, run_finished_at)

        # Generate and save JSON output
        output = generate_run_output(
            run_id=run_id,
            queue_entry=task_info,
            task_def=task_def,
            worker_id=worker_id,
            started_at=run_started_at,
            finished_at=run_finished_at,
            status=status,
            exec_result=exec_result,
            merged_params=params,
            fanout_records=fanout_records,
        )

        output_path = save_run_output(runs_dir, task_id, run_id, output)

        if verbose:
            print(f"Status: {status}")
            print(f"Exit code: {exec_result.exit_code}")
            print(f"Wall time: {exec_result.cost.wall_ms}ms")
            print(f"Output saved to: {output_path}")
            if exec_result.stdout:
                print(f"--- stdout ---\n{exec_result.stdout}")
            if exec_result.stderr:
                print(f"--- stderr ---\n{exec_result.stderr}")

        return 0 if status == "done" else 2

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Task Runner - Execute tasks from SQLite queue"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Run exactly one task then exit (default behavior)"
    )

    args = parser.parse_args()
    config = get_config()

    # Ensure runs directory exists
    Path(config["runs_dir"]).mkdir(parents=True, exist_ok=True)

    exit_code = run_once(config, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
