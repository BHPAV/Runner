import os
import json
import sys
from pathlib import Path

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Parameters
search_path = params.get('search_path', os.path.expanduser('~/Downloads'))
extensions = params.get('extensions', ['.csv', '.yaml', '.yml', '.xml', '.md', '.txt', '.py', '.ts', '.js'])
limit = params.get('limit', 20)
neo4j_uri = params.get('neo4j_uri', os.environ.get('NEO4J_URI', 'bolt://localhost:7687'))
neo4j_user = params.get('neo4j_user', os.environ.get('NEO4J_USER', 'neo4j'))
neo4j_password = params.get('neo4j_password', os.environ.get('NEO4J_PASSWORD', ''))
neo4j_database = params.get('neo4j_database', os.environ.get('NEO4J_DATABASE', 'neo4j'))

# Try to import neo4j driver
try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

# Normalize extensions to lowercase with leading dot
normalized_extensions = set()
for ext in extensions:
    if not ext.startswith('.'):
        ext = '.' + ext
    normalized_extensions.add(ext.lower())

# Scan filesystem for files with specified extensions
print(f"Scanning {search_path} for files with extensions: {sorted(normalized_extensions)}...", file=sys.stderr)
files_by_ext = {}
all_files = []
skip_dirs = {'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build', 'Library', 'Applications', '.git', 'runs'}

search_path = os.path.expanduser(search_path)

try:
    for root, dirs, files in os.walk(search_path):
        # Skip hidden directories and common non-essential dirs
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs]

        for f in files:
            if f.startswith('.'):
                continue

            file_ext = os.path.splitext(f)[1].lower()
            if file_ext in normalized_extensions:
                full_path = os.path.join(root, f)
                try:
                    size = os.path.getsize(full_path)
                    if size < 10_000_000:  # Skip files > 10MB
                        file_info = {
                            'path': full_path,
                            'name': f,
                            'size': size,
                            'extension': file_ext
                        }
                        all_files.append(file_info)

                        if file_ext not in files_by_ext:
                            files_by_ext[file_ext] = []
                        files_by_ext[file_ext].append(file_info)
                except:
                    pass

        # Limit total files scanned
        if len(all_files) > 5000:
            break
except Exception as e:
    print(f"Scan error: {e}", file=sys.stderr)

print(f"Found {len(all_files)} files on filesystem", file=sys.stderr)
for ext, files in sorted(files_by_ext.items()):
    print(f"  {ext}: {len(files)} files", file=sys.stderr)

# Check which files are already in Neo4j
recorded_doc_ids = set()
recorded_source_files = set()

if HAS_NEO4J and all_files:
    try:
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        driver.verify_connectivity()
        print(f"Connected to Neo4j ({neo4j_database})", file=sys.stderr)

        with driver.session(database=neo4j_database) as session:
            # Get all doc_ids from Data nodes (root level)
            result = session.run("""
                MATCH (d:Data) WHERE d.path = '/root'
                RETURN d.doc_id AS doc_id
            """)
            for record in result:
                recorded_doc_ids.add(record['doc_id'])

            # Get source_file values from Data nodes
            result = session.run("""
                MATCH (d:Data) WHERE d.key = 'source_file' AND d.value_str IS NOT NULL
                RETURN d.value_str AS source_file
            """)
            for record in result:
                recorded_source_files.add(record['source_file'])

            # Also check File nodes if they exist
            result = session.run("""
                MATCH (f:File) WHERE f.path IS NOT NULL
                RETURN f.path AS path
            """)
            for record in result:
                recorded_source_files.add(record['path'])

        driver.close()
        print(f"Found {len(recorded_doc_ids)} doc_ids and {len(recorded_source_files)} source files in graph", file=sys.stderr)

    except Exception as e:
        print(f"Neo4j error: {e}", file=sys.stderr)

elif not HAS_NEO4J:
    print("neo4j Python driver not installed, skipping graph check", file=sys.stderr)

# Find unrecorded files
unrecorded_files = []
unrecorded_files_by_ext = {}

for file_info in all_files:
    path = file_info['path']
    name = file_info['name']
    ext = file_info['extension']

    # Generate doc_id the same way converters do
    doc_id = name.replace('.', '_').replace(' ', '_')

    # Check if already recorded
    is_recorded = (
        doc_id in recorded_doc_ids or
        path in recorded_source_files or
        name in recorded_doc_ids
    )

    if not is_recorded:
        unrecorded_files.append(path)
        if ext not in unrecorded_files_by_ext:
            unrecorded_files_by_ext[ext] = []
        unrecorded_files_by_ext[ext].append(path)

        if len(unrecorded_files) >= limit:
            break

print(f"Found {len(unrecorded_files)} unrecorded files", file=sys.stderr)

# Create push tasks for batch_convert_files_task
push_tasks = []
if unrecorded_files:
    push_tasks.append({
        'task_id': 'batch_convert_files_task',
        'parameters': {
            'files_by_ext': unrecorded_files_by_ext,
            'neo4j_uri': neo4j_uri,
            'neo4j_user': neo4j_user,
            'neo4j_password': neo4j_password,
            'neo4j_database': neo4j_database
        },
        'reason': f'Convert {len(unrecorded_files)} unrecorded files to JSON and upload to Neo4j'
    })

task_result = {
    '__task_result__': True,
    'output': {
        'unrecorded_files': unrecorded_files,
        'unrecorded_files_by_ext': unrecorded_files_by_ext,
        'files_scanned': len(all_files),
        'unrecorded_count': len(unrecorded_files),
        'recorded_doc_ids': len(recorded_doc_ids),
        'recorded_source_files': len(recorded_source_files)
    },
    'variables': {
        'unrecorded_files': unrecorded_files,
        'unrecorded_files_by_ext': unrecorded_files_by_ext,
        'files_scan_complete': True
    },
    'decisions': [
        f"Scanned {len(all_files)} files under {search_path}",
        f"Extensions: {sorted(normalized_extensions)}",
        f"Graph has {len(recorded_doc_ids)} doc_ids and {len(recorded_source_files)} source files",
        f"Found {len(unrecorded_files)} unrecorded files (limit: {limit})"
    ],
    'push_tasks': push_tasks
}

print(json.dumps(task_result))
