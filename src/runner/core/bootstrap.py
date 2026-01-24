#!/usr/bin/env python3
"""
Database initialization script for the task runner.
Creates tables, inserts default control flags, and optionally seeds test tasks.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path


def get_db_path() -> str:
    """Get database path from environment or default."""
    return os.environ.get("TASK_DB", "./tasks.db")


def init_schema(conn: sqlite3.Connection, schema_path: str = "./schema.sql") -> None:
    """Initialize database with schema from SQL file."""
    schema_file = Path(schema_path)
    if not schema_file.exists():
        print(f"Error: Schema file not found: {schema_path}", file=sys.stderr)
        sys.exit(1)

    schema_sql = schema_file.read_text()
    conn.executescript(schema_sql)
    conn.commit()
    print(f"Schema initialized from {schema_path}")


def init_control_flags(conn: sqlite3.Connection) -> None:
    """Insert default control flags."""
    defaults = [
        ("kill_all", "0"),
        ("pause_new_tasks", "0"),
    ]

    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO control_flags (key, value) VALUES (?, ?)",
            (key, value)
        )
    conn.commit()
    print("Default control flags initialized")


def seed_test_tasks(conn: sqlite3.Connection) -> None:
    """Insert sample tasks for testing."""
    test_tasks = [
        {
            "task_id": "hello_cli",
            "task_type": "cli",
            "code": "echo 'Hello from CLI! Param: {greeting}'",
            "parameters_json": json.dumps({"greeting": "World"}),
            "timeout_seconds": 60,
        },
        {
            "task_id": "hello_python",
            "task_type": "python",
            "code": """
import os
import json
params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
print(f"Hello from Python! Param: {params.get('name', 'Anonymous')}")
""".strip(),
            "parameters_json": json.dumps({"name": "PythonUser"}),
            "timeout_seconds": 60,
        },
        {
            "task_id": "hello_typescript",
            "task_type": "typescript",
            "code": """
const params = JSON.parse(process.env.TASK_PARAMS || '{}');
console.log(`Hello from TypeScript! Param: ${params.message || 'None'}`);
""".strip(),
            "parameters_json": json.dumps({"message": "TSMessage"}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "compute_intensive",
            "task_type": "python",
            "code": """
import os
import json
params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
n = int(params.get('iterations', 1000000))
total = sum(i * i for i in range(n))
print(f"Computed sum of squares up to {n}: {total}")
""".strip(),
            "parameters_json": json.dumps({"iterations": 100000}),
            "timeout_seconds": 300,
        },
        {
            "task_id": "fanout_example",
            "task_type": "python",
            "code": """
import os
import json
import sqlite3

# This task demonstrates fan-out by inserting child tasks
params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
db_path = os.environ.get('TASK_DB', './tasks.db')
queue_id = int(os.environ.get('TASK_QUEUE_ID', '0'))

if queue_id == 0:
    print("No queue_id provided, skipping fan-out")
else:
    conn = sqlite3.connect(db_path)
    count = int(params.get('child_count', 3))

    for i in range(count):
        conn.execute('''
            INSERT INTO task_fanout (parent_queue_id, child_task_id, child_parameters_json, created_at)
            VALUES (?, 'hello_cli', ?, datetime('now'))
        ''', (queue_id, json.dumps({"greeting": f"Child-{i}"})))

    conn.commit()
    conn.close()
    print(f"Created {count} fan-out tasks")
""".strip(),
            "parameters_json": json.dumps({"child_count": 3}),
            "timeout_seconds": 60,
        },
        {
            "task_id": "spawn_task",
            "task_type": "python",
            "code": """
import os
import json
import sqlite3
import uuid
from datetime import datetime, timezone

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
db_path = os.environ.get('TASK_DB', './tasks.db')

# Get parameters for the new task
task_name = params.get('task_name', f'spawned_{uuid.uuid4().hex[:8]}')
task_type = params.get('spawn_type', 'python')
task_code = params.get('spawn_code', 'print("Hello from spawned task!")')
task_params = params.get('spawn_params', {})
task_timeout = params.get('spawn_timeout', 60)

conn = sqlite3.connect(db_path)

# Create the new task definition
conn.execute('''
    INSERT OR REPLACE INTO tasks (task_id, task_type, code, parameters_json, timeout_seconds, enabled)
    VALUES (?, ?, ?, ?, ?, 1)
''', (task_name, task_type, task_code, json.dumps(task_params), task_timeout))

# Queue the new task with a unique request_id
request_id = str(uuid.uuid4())
cur = conn.execute('''
    INSERT INTO task_queue (request_id, task_id, status, enqueued_at, parameters_json)
    VALUES (?, ?, 'queued', ?, ?)
''', (request_id, task_name, datetime.now(timezone.utc).isoformat(), json.dumps(task_params)))

queue_id = cur.lastrowid
conn.commit()
conn.close()

print(f"Created and queued task '{task_name}' (queue_id={queue_id}, request_id={request_id})")
print(f"  Type: {task_type}")
print(f"  Code: {task_code[:50]}{'...' if len(task_code) > 50 else ''}")
""".strip(),
            "parameters_json": json.dumps({
                "task_name": "dynamic_greeting",
                "spawn_type": "cli",
                "spawn_code": "echo 'Hello from a dynamically created task!'",
                "spawn_params": {},
                "spawn_timeout": 30
            }),
            "timeout_seconds": 60,
        },
        {
            "task_id": "claude_planner",
            "task_type": "python",
            "code": """
import os
import json
import subprocess
import sys

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))

# Get parameters
prompt = params.get('prompt', 'Create a plan for building a simple web server')
working_dir = params.get('working_dir', os.getcwd())
model = params.get('model', '')  # Empty string uses default
output_format = params.get('output_format', 'json')

# Build the claude command for headless planning mode
cmd = [
    'claude',
    '-p', prompt,                       # Headless mode with prompt
    '--permission-mode', 'plan',        # Enable planning mode
    '--output-format', output_format,
]

# Add optional model override
if model:
    cmd.extend(['--model', model])

print(f"Running Claude Code planner...")
print(f"Prompt: {prompt}")
print(f"Working directory: {working_dir}")
print("-" * 50)

try:
    result = subprocess.run(
        cmd,
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout for planning
    )

    print("STDOUT:")
    print(result.stdout)

    if result.stderr:
        print("STDERR:")
        print(result.stderr, file=sys.stderr)

    # Try to parse JSON output
    if output_format == 'json' and result.stdout.strip():
        try:
            output_data = json.loads(result.stdout)
            print("-" * 50)
            print("Parsed JSON output successfully")
            if 'plan' in output_data:
                print(f"Plan steps: {len(output_data.get('plan', []))}")
        except json.JSONDecodeError:
            print("Output was not valid JSON")

    sys.exit(result.returncode)

except subprocess.TimeoutExpired:
    print("ERROR: Claude Code timed out", file=sys.stderr)
    sys.exit(1)
except FileNotFoundError:
    print("ERROR: 'claude' command not found. Is Claude Code installed?", file=sys.stderr)
    sys.exit(1)
""".strip(),
            "parameters_json": json.dumps({
                "prompt": "Create a plan for implementing a REST API with user authentication",
                "working_dir": ".",
                "model": "",
                "output_format": "json"
            }),
            "timeout_seconds": 600,
        },
        # =====================================================================
        # Stack Runner Tasks - demonstrate LIFO execution with context
        # =====================================================================
        {
            "task_id": "stack_planner",
            "task_type": "python",
            "code": """
import os
import json

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

problem = params.get('problem', 'solve something')
steps = params.get('steps', ['analyze', 'implement', 'verify'])

# This task decomposes the problem into steps
# Each step will be pushed onto the stack and executed in order

push_tasks = []
for i, step in enumerate(steps):
    push_tasks.append({
        "task_id": f"stack_step_{step}",
        "parameters": {"step_name": step, "step_index": i, "problem": problem},
        "reason": f"Step {i+1}: {step}"
    })

result = {
    "__task_result__": True,
    "output": f"Decomposed '{problem}' into {len(steps)} steps",
    "variables": {"problem": problem, "total_steps": len(steps)},
    "decisions": [f"Will execute steps: {steps}"],
    "push_tasks": push_tasks
}

print(json.dumps(result))
""".strip(),
            "parameters_json": json.dumps({
                "problem": "build a feature",
                "steps": ["analyze", "implement", "verify"]
            }),
            "timeout_seconds": 60,
        },
        {
            "task_id": "stack_step_analyze",
            "task_type": "python",
            "code": """
import os
import json

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

step_name = params.get('step_name', 'analyze')
problem = params.get('problem', 'unknown')

# Read from context
variables = context.get('variables', {})
previous_outputs = context.get('outputs', [])

# Simulate analysis
findings = [f"Found 3 components for: {problem}", "Dependencies identified", "Risks assessed"]

result = {
    "__task_result__": True,
    "output": {"phase": "analysis", "findings": findings},
    "variables": {"analysis_complete": True, "component_count": 3},
    "decisions": ["Proceeding with implementation based on analysis"]
}

print(json.dumps(result))
""".strip(),
            "parameters_json": json.dumps({}),
            "timeout_seconds": 60,
        },
        {
            "task_id": "stack_step_implement",
            "task_type": "python",
            "code": """
import os
import json

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Read from context - we can see analysis results!
variables = context.get('variables', {})
analysis_done = variables.get('analysis_complete', False)
component_count = variables.get('component_count', 0)

if not analysis_done:
    result = {
        "__task_result__": True,
        "output": {"error": "Cannot implement without analysis"},
        "errors": ["Analysis not complete"],
        "abort": True
    }
else:
    # Simulate implementation
    result = {
        "__task_result__": True,
        "output": {"phase": "implementation", "components_built": component_count},
        "variables": {"implementation_complete": True, "artifacts": ["module_a.py", "module_b.py"]},
        "decisions": [f"Built {component_count} components based on analysis"]
    }

print(json.dumps(result))
""".strip(),
            "parameters_json": json.dumps({}),
            "timeout_seconds": 60,
        },
        {
            "task_id": "stack_step_verify",
            "task_type": "python",
            "code": """
import os
import json

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Read accumulated context
variables = context.get('variables', {})
outputs = context.get('outputs', [])
decisions = context.get('decisions', [])

impl_done = variables.get('implementation_complete', False)
artifacts = variables.get('artifacts', [])

# Summarize the entire execution
summary = {
    "total_phases": len(outputs),
    "artifacts_produced": artifacts,
    "decisions_made": len(decisions),
    "all_variables": variables
}

result = {
    "__task_result__": True,
    "output": {"phase": "verification", "summary": summary, "status": "PASSED" if impl_done else "FAILED"},
    "variables": {"verification_complete": True, "final_status": "success"},
    "decisions": ["All phases completed successfully" if impl_done else "Verification failed"]
}

print(json.dumps(result))
""".strip(),
            "parameters_json": json.dumps({}),
            "timeout_seconds": 60,
        },
        {
            "task_id": "stack_recursive",
            "task_type": "python",
            "code": """
import os
import json

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

n = params.get('n', 3)
variables = context.get('variables', {})
current_sum = variables.get('running_sum', 0)

# Recursive countdown - each call pushes another if n > 0
new_sum = current_sum + n

result = {
    "__task_result__": True,
    "output": {"n": n, "added": n, "running_sum": new_sum},
    "variables": {"running_sum": new_sum, f"step_{n}": True},
    "decisions": [f"Added {n} to sum, now {new_sum}"],
    "push_tasks": []
}

if n > 1:
    result["push_tasks"].append({
        "task_id": "stack_recursive",
        "parameters": {"n": n - 1},
        "reason": f"Continue countdown from {n-1}"
    })
else:
    result["variables"]["final_sum"] = new_sum
    result["decisions"].append(f"Recursion complete! Final sum: {new_sum}")

print(json.dumps(result))
""".strip(),
            "parameters_json": json.dumps({"n": 5}),
            "timeout_seconds": 60,
        },
        # =====================================================================
        # File Type Converter Tasks - for Neo4j ingestion pipeline
        # =====================================================================
        {
            "task_id": "find_unrecorded_files_task",
            "task_type": "python_file",
            "code": "find_unrecorded_files_task.py",
            "parameters_json": json.dumps({
                "search_path": "~/Downloads",
                "extensions": [".csv", ".yaml", ".yml", ".xml", ".md", ".txt", ".py", ".ts", ".js"],
                "limit": 20
            }),
            "timeout_seconds": 600,
        },
        {
            "task_id": "batch_convert_files_task",
            "task_type": "python_file",
            "code": "batch_convert_files_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 300,
        },
        {
            "task_id": "csv_to_json_task",
            "task_type": "python_file",
            "code": "csv_to_json_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "yaml_to_json_task",
            "task_type": "python_file",
            "code": "yaml_to_json_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "xml_to_json_task",
            "task_type": "python_file",
            "code": "xml_to_json_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "markdown_to_json_task",
            "task_type": "python_file",
            "code": "markdown_to_json_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "text_to_json_task",
            "task_type": "python_file",
            "code": "text_to_json_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "python_to_json_task",
            "task_type": "python_file",
            "code": "python_to_json_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "code_to_json_task",
            "task_type": "python_file",
            "code": "code_to_json_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 120,
        },
        {
            "task_id": "upload_jsongraph",
            "task_type": "python_file",
            "code": "upload_jsongraph_task.py",
            "parameters_json": json.dumps({}),
            "timeout_seconds": 600,
        },
    ]

    for task in test_tasks:
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks
            (task_id, task_type, code, parameters_json, working_dir, env_json, timeout_seconds, enabled)
            VALUES (?, ?, ?, ?, NULL, '{}', ?, 1)
            """,
            (
                task["task_id"],
                task["task_type"],
                task["code"],
                task["parameters_json"],
                task["timeout_seconds"],
            )
        )
    conn.commit()
    print(f"Seeded {len(test_tasks)} test tasks")


def queue_task(
    conn: sqlite3.Connection,
    task_id: str,
    parameters_json: str = "{}",
    request_id: str = None
) -> dict:
    """
    Queue a task with idempotency support.

    Returns dict: {queue_id, request_id, is_duplicate, status}
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    # Check for duplicate
    cur = conn.execute(
        "SELECT queue_id, status FROM task_queue WHERE request_id = ?",
        (request_id,)
    )
    existing = cur.fetchone()

    if existing:
        return {
            "queue_id": existing["queue_id"],
            "request_id": request_id,
            "is_duplicate": True,
            "status": existing["status"],
        }

    cur = conn.execute(
        """
        INSERT INTO task_queue (request_id, task_id, status, enqueued_at, parameters_json)
        VALUES (?, ?, 'queued', datetime('now'), ?)
        """,
        (request_id, task_id, parameters_json)
    )
    conn.commit()

    return {
        "queue_id": cur.lastrowid,
        "request_id": request_id,
        "is_duplicate": False,
        "status": "queued",
    }


def main():
    parser = argparse.ArgumentParser(description="Initialize task runner database")
    parser.add_argument(
        "--db",
        default=None,
        help="Database path (default: TASK_DB env or ./tasks.db)"
    )
    parser.add_argument(
        "--schema",
        default="./schema.sql",
        help="Path to schema.sql file"
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed database with test tasks"
    )
    parser.add_argument(
        "--queue",
        metavar="TASK_ID",
        help="Queue a specific task for execution"
    )
    parser.add_argument(
        "--queue-params",
        default="{}",
        help="JSON parameters for queued task"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables (WARNING: destroys data)"
    )

    args = parser.parse_args()

    db_path = args.db or get_db_path()

    # Resolve schema path relative to script location if not absolute
    schema_path = args.schema
    if not Path(schema_path).is_absolute():
        script_dir = Path(__file__).parent
        schema_path = script_dir / schema_path

    print(f"Using database: {db_path}")

    # Ensure runs directory exists
    runs_dir = os.environ.get("RUNS_DIR", "./runs")
    Path(runs_dir).mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        if args.reset:
            print("Resetting database...")
            conn.executescript("""
                DROP TABLE IF EXISTS task_fanout;
                DROP TABLE IF EXISTS task_queue;
                DROP TABLE IF EXISTS control_flags;
                DROP TABLE IF EXISTS tasks;
            """)
            conn.commit()

        init_schema(conn, str(schema_path))
        init_control_flags(conn)

        if args.seed:
            seed_test_tasks(conn)

        if args.queue:
            result = queue_task(conn, args.queue, args.queue_params)
            if result["is_duplicate"]:
                print(f"Task '{args.queue}' already queued (request_id={result['request_id']}, queue_id={result['queue_id']}, status={result['status']})")
            else:
                print(f"Queued task '{args.queue}' with queue_id={result['queue_id']}, request_id={result['request_id']}")

        # Print summary
        cur = conn.execute("SELECT COUNT(*) FROM tasks")
        task_count = cur.fetchone()[0]

        cur = conn.execute("SELECT COUNT(*) FROM task_queue WHERE status='queued'")
        queued_count = cur.fetchone()[0]

        print(f"\nDatabase summary:")
        print(f"  Total tasks defined: {task_count}")
        print(f"  Tasks in queue: {queued_count}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
