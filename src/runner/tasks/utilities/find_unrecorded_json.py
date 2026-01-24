import os
import json
import subprocess
import sys
import re
from pathlib import Path

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Parameters
search_path = params.get('search_path', os.path.expanduser('~/Downloads'))
limit = params.get('limit', 20)
neo4j_uri = params.get('neo4j_uri', os.environ.get('NEO4J_URI', 'bolt://localhost:7687'))
neo4j_user = params.get('neo4j_user', os.environ.get('NEO4J_USER', 'neo4j'))
neo4j_password = params.get('neo4j_password', os.environ.get('NEO4J_PASSWORD', ''))
neo4j_database = params.get('neo4j_database', os.environ.get('NEO4J_DATABASE', 'neo4j'))

# First, scan filesystem for JSON files
print("Scanning filesystem for JSON files...", file=sys.stderr)
json_files = []
skip_dirs = {'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build', 'Library', 'Applications', '.git'}

try:
    for root, dirs, files in os.walk(search_path):
        # Skip hidden directories and common non-essential dirs
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs]

        for f in files:
            if f.endswith('.json') and not f.startswith('.'):
                full_path = os.path.join(root, f)
                try:
                    size = os.path.getsize(full_path)
                    if size < 10_000_000:  # Skip files > 10MB
                        json_files.append({
                            'path': full_path,
                            'name': f,
                            'size': size
                        })
                except:
                    pass

        # Limit total files scanned
        if len(json_files) > 5000:
            break
except Exception as e:
    print(f"Scan error: {e}", file=sys.stderr)

print(f"Found {len(json_files)} JSON files on filesystem", file=sys.stderr)

# Build the prompt for Claude Code
file_list = '\n'.join(f['path'] for f in json_files[:100])

prompt = """You are helping to find JSON files on the filesystem that are NOT yet recorded in a Neo4j graph database.

## Neo4j Connection
- URI: {neo4j_uri}
- User: {neo4j_user}
- Password: {neo4j_password}
- Database: {neo4j_database}

## Graph Schema
The graph stores file information in two ways:

1. **File nodes** (from directory mapping):
   - `(:File {{path, name, size, content, extension}})`
   - These have the full file path in the `path` property

2. **Data nodes** (JSON graph format):
   - `(:Data {{doc_id, path, kind, key, value_str, ...}})`
   - Files might be stored with `key = 'path'` and the file path in `value_str`

## Task
I have found {file_count} JSON files on the filesystem under `{search_path}`.

Here are the file paths found:
```
{file_list}
```

Please:
1. Connect to Neo4j and write a Cypher query that checks which of these paths are NOT already recorded
2. Check the `:File` nodes by their `path` property
3. Also check `:Data` nodes where `value_str` might contain file paths
4. Return at most {limit} unrecorded files

Execute the query and output your final answer as JSON:
```json
{{
  "unrecorded_files": ["path1", "path2", ...],
  "total_scanned": <number>,
  "total_in_graph": <number>,
  "cypher_query": "<the query you used>"
}}
```
""".format(
    neo4j_uri=neo4j_uri,
    neo4j_user=neo4j_user,
    neo4j_password=neo4j_password,
    neo4j_database=neo4j_database,
    file_count=len(json_files),
    search_path=search_path,
    file_list=file_list,
    limit=limit
)

# Run Claude Code in headless mode
print("Running Claude Code agent...", file=sys.stderr)

try:
    result = subprocess.run(
        [
            'claude',
            '-p', prompt,
            '--output-format', 'text',
            '--max-turns', '10'
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env={
            **os.environ,
            'NEO4J_URI': neo4j_uri,
            'NEO4J_USER': neo4j_user,
            'NEO4J_PASSWORD': neo4j_password,
            'NEO4J_DATABASE': neo4j_database
        }
    )

    agent_output = result.stdout
    agent_stderr = result.stderr
    exit_code = result.returncode

except subprocess.TimeoutExpired:
    agent_output = ""
    agent_stderr = "Claude Code timed out after 300 seconds"
    exit_code = -1
except FileNotFoundError:
    agent_output = ""
    agent_stderr = "Claude Code CLI not found. Is it installed?"
    exit_code = -2

# Try to extract JSON result from agent output
unrecorded_files = []
cypher_query = ""
total_in_graph = 0

# Look for JSON block in output
json_pattern = r'\{[^{}]*"unrecorded_files"\s*:\s*\[[^\]]*\][^{}]*\}'
json_match = re.search(json_pattern, agent_output, re.DOTALL)

if json_match:
    try:
        # Clean up the match for parsing
        json_str = json_match.group()
        parsed = json.loads(json_str)
        unrecorded_files = parsed.get('unrecorded_files', [])
        cypher_query = parsed.get('cypher_query', '')
        total_in_graph = parsed.get('total_in_graph', 0)
    except json.JSONDecodeError:
        pass

# If no structured output, try to find file paths in the output
if not unrecorded_files:
    # Look for paths that look like JSON files
    path_matches = re.findall(r'(/[^\s"\'<>\[\]]+\.json)', agent_output)
    # Filter to only paths that were in our original scan
    scanned_paths = set(f['path'] for f in json_files)
    unrecorded_files = [p for p in set(path_matches) if p in scanned_paths][:limit]

task_result = {
    '__task_result__': True,
    'output': {
        'unrecorded_files': unrecorded_files[:limit],
        'files_scanned': len(json_files),
        'unrecorded_count': len(unrecorded_files),
        'total_in_graph': total_in_graph,
        'cypher_query': cypher_query,
        'agent_exit_code': exit_code
    },
    'variables': {
        'unrecorded_json_files': unrecorded_files[:limit],
        'json_scan_complete': True
    },
    'decisions': [
        f"Scanned {len(json_files)} JSON files under {search_path}",
        f"Found {len(unrecorded_files)} files not in graph (limited to {limit})",
        f"Claude Code agent exit code: {exit_code}"
    ],
    'metadata': {
        'agent_stdout': agent_output[:8000] if agent_output else '',
        'agent_stderr': agent_stderr[:2000] if agent_stderr else ''
    }
}

print(json.dumps(task_result))
