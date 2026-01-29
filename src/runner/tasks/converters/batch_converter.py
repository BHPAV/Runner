import os
import json
import sys
from pathlib import Path

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Parameters
files_by_ext = params.get('files_by_ext', {})
file_paths = params.get('file_paths', [])
neo4j_uri = params.get('neo4j_uri', os.environ.get('NEO4J_URI', 'bolt://localhost:7687'))
neo4j_user = params.get('neo4j_user', os.environ.get('NEO4J_USER', 'neo4j'))
neo4j_password = params.get('neo4j_password', os.environ.get('NEO4J_PASSWORD', ''))
neo4j_database = params.get('neo4j_database', os.environ.get('NEO4J_DATABASE', 'neo4j'))
max_content_length = params.get('max_content_length', 5000)

# If files_by_ext not provided, try to get from context
if not files_by_ext:
    variables = context.get('variables', {})
    files_by_ext = variables.get('unrecorded_files_by_ext', {})

# If still empty but we have flat file_paths, organize by extension
if not files_by_ext and file_paths:
    for path in file_paths:
        ext = os.path.splitext(path)[1].lower()
        if ext not in files_by_ext:
            files_by_ext[ext] = []
        files_by_ext[ext].append(path)

if not files_by_ext:
    result = {
        '__task_result__': True,
        'output': {'error': 'No files to convert'},
        'errors': ['No files_by_ext in params or unrecorded_files_by_ext in context'],
        'abort': True
    }
    print(json.dumps(result))
    sys.exit(0)

# Converter mapping: extension -> task_id
CONVERTER_MAP = {
    '.csv': 'csv_to_json_task',
    '.yaml': 'yaml_to_json_task',
    '.yml': 'yaml_to_json_task',
    '.xml': 'xml_to_json_task',
    '.md': 'markdown_to_json_task',
    '.txt': 'text_to_json_task',
    '.py': 'python_to_json_task',
    '.ts': 'code_to_json_task',
    '.tsx': 'code_to_json_task',
    '.js': 'code_to_json_task',
    '.jsx': 'code_to_json_task',
}

# Create push tasks for each file
push_tasks = []
total_files = 0
files_by_converter = {}

for ext, paths in files_by_ext.items():
    ext_lower = ext.lower()
    if not ext_lower.startswith('.'):
        ext_lower = '.' + ext_lower

    task_id = CONVERTER_MAP.get(ext_lower)

    if not task_id:
        print(f"Warning: No converter for extension {ext}, skipping {len(paths)} files", file=sys.stderr)
        continue

    if task_id not in files_by_converter:
        files_by_converter[task_id] = []

    for path in paths:
        files_by_converter[task_id].append(path)
        total_files += 1

        push_tasks.append({
            'task_id': task_id,
            'parameters': {
                'source_path': path,
                'neo4j_uri': neo4j_uri,
                'neo4j_user': neo4j_user,
                'neo4j_password': neo4j_password,
                'neo4j_database': neo4j_database,
                'max_content_length': max_content_length
            },
            'reason': f'Convert {os.path.basename(path)}'
        })

# Summary by converter
summary = {}
for task_id, paths in files_by_converter.items():
    summary[task_id] = len(paths)

result = {
    '__task_result__': True,
    'output': {
        'total_files': total_files,
        'converters_used': list(files_by_converter.keys()),
        'files_per_converter': summary,
        'extensions_processed': list(files_by_ext.keys())
    },
    'variables': {
        'batch_convert_started': True,
        'total_files_to_convert': total_files
    },
    'decisions': [
        f"Routing {total_files} files to {len(files_by_converter)} converters",
        *[f"{task_id}: {count} files" for task_id, count in summary.items()]
    ],
    'push_tasks': push_tasks
}

print(json.dumps(result))
