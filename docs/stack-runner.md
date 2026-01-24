# Stack Runner

The Stack Runner is a LIFO (Last-In-First-Out) task execution engine with monadic context accumulation.

## Concepts

### LIFO Execution

Unlike traditional FIFO queues, the stack runner processes the most recently added task first. This enables:

- **Depth-first execution**: Child tasks complete before siblings
- **Dynamic task graphs**: Tasks can push new tasks during execution
- **Context propagation**: Each task receives accumulated context from previous executions

### Monadic Context

The `StackContext` flows through execution, accumulating:

```python
@dataclass
class StackContext:
    variables: dict      # Named values accessible to all tasks
    outputs: list        # All task outputs
    decisions: list      # Audit trail of decisions made
    errors: list         # Any errors encountered
    metadata: dict       # Arbitrary metadata
```

Tasks can read from context and contribute to it via their output.

## Usage

### Start a New Stack

```bash
python stack_runner.py start <task_id> [--params '{}'] [--request-id <id>]
```

**Examples:**

```bash
# Simple task
python stack_runner.py -v start hello_cli

# With parameters
python stack_runner.py -v start upload_dual \
  --params '{"json_path": "data.json", "doc_id": "my_doc"}'

# With idempotency key
python stack_runner.py start my_task --request-id "unique-key-123"
```

### Resume an Existing Stack

```bash
python stack_runner.py resume <stack_id>
```

### Run One Step

```bash
python stack_runner.py run-one <stack_id>
```

### Check Status

```bash
python stack_runner.py status <stack_id>
```

## Task Output Format

Tasks communicate with the runner via JSON output to stdout:

```json
{
  "__task_result__": true,
  "output": { "result": "value" },
  "variables": { "my_var": 123 },
  "decisions": ["Decided to do X"],
  "errors": [],
  "metadata": {},
  "push_tasks": [
    {
      "task_id": "next_task",
      "parameters": { "param": "value" },
      "reason": "Why this task was pushed"
    }
  ],
  "abort": false
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `__task_result__` | bool | Must be `true` to be recognized as structured output |
| `output` | any | Direct output value |
| `variables` | dict | Variables to add to context |
| `decisions` | list | Audit trail entries |
| `errors` | list | Error messages |
| `metadata` | dict | Additional metadata |
| `push_tasks` | list | New tasks to push onto the stack |
| `abort` | bool | If true, abort the entire stack |

## Task Types

### CLI Tasks

Execute shell commands with parameter substitution:

```python
{
    "task_id": "my_cli_task",
    "task_type": "cli",
    "code": "echo 'Hello {name}'",
    "parameters_json": '{"name": "World"}'
}
```

### Python Tasks

Execute inline Python code:

```python
{
    "task_id": "my_python_task",
    "task_type": "python",
    "code": """
import os
import json
params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
print(f"Hello {params.get('name', 'World')}")
"""
}
```

### Python File Tasks

Execute a Python file:

```python
{
    "task_id": "my_file_task",
    "task_type": "python_file",
    "code": "my_script.py"  # Relative to runner directory
}
```

### TypeScript Tasks

Execute TypeScript via ts-node:

```python
{
    "task_id": "my_ts_task",
    "task_type": "typescript",
    "code": """
const params = JSON.parse(process.env.TASK_PARAMS || '{}');
console.log(`Hello ${params.name || 'World'}`);
"""
}
```

## Environment Variables for Tasks

Tasks receive these environment variables:

| Variable | Description |
|----------|-------------|
| `TASK_PARAMS` | JSON-encoded task parameters |
| `TASK_CONTEXT` | JSON-encoded stack context |
| `TASK_QUEUE_ID` | Current queue entry ID |
| `TASK_STACK_ID` | Current stack ID |
| `TASK_DB` | Path to SQLite database |

## Execution Trace

Each stack execution produces a JSON trace file in the `runs/` directory:

```json
{
  "stack_id": "uuid",
  "status": "done",
  "created_at": "2026-01-24T12:00:00Z",
  "finished_at": "2026-01-24T12:00:05Z",
  "initial_task_id": "my_task",
  "final_context": { ... },
  "trace": [
    {
      "queue_id": 1,
      "task_id": "my_task",
      "depth": 0,
      "status": "done",
      "execution_ms": 123,
      "input_context": { ... },
      "output": { ... },
      "pushed_tasks": [ ... ]
    }
  ]
}
```

## Example: Recursive Task

A task that pushes itself with decremented parameter:

```python
import os
import json

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

n = params.get('n', 5)
current_sum = context.get('variables', {}).get('running_sum', 0)
new_sum = current_sum + n

result = {
    "__task_result__": True,
    "output": {"n": n, "running_sum": new_sum},
    "variables": {"running_sum": new_sum},
    "decisions": [f"Added {n}, total is {new_sum}"],
    "push_tasks": []
}

if n > 1:
    result["push_tasks"].append({
        "task_id": "stack_recursive",
        "parameters": {"n": n - 1},
        "reason": f"Continue countdown from {n-1}"
    })

print(json.dumps(result))
```
