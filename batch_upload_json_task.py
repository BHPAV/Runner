import os
import json
import sys

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Get file list from params or context
file_paths = params.get('file_paths', [])

# If no paths provided, try to get from context (from previous find_unrecorded_json task)
if not file_paths:
    variables = context.get('variables', {})
    file_paths = variables.get('unrecorded_json_files', [])

# Parameters for Neo4j connection (passed to child tasks)
neo4j_uri = params.get('neo4j_uri', os.environ.get('NEO4J_URI', 'bolt://localhost:7687'))
neo4j_user = params.get('neo4j_user', os.environ.get('NEO4J_USER', 'neo4j'))
neo4j_password = params.get('neo4j_password', os.environ.get('NEO4J_PASSWORD', ''))
neo4j_database = params.get('neo4j_database', os.environ.get('NEO4J_DATABASE', 'neo4j'))
max_content_length = params.get('max_content_length', 5000)

if not file_paths:
    result = {
        '__task_result__': True,
        'output': {'error': 'No file paths provided'},
        'errors': ['No file_paths in params or unrecorded_json_files in context'],
        'abort': True
    }
    print(json.dumps(result))
    sys.exit(0)

# Create a push task for each file
push_tasks = []
for file_path in file_paths:
    # Generate doc_id from filename
    basename = os.path.basename(file_path)
    doc_id = os.path.splitext(basename)[0]

    push_tasks.append({
        'task_id': 'upload_jsongraph',
        'parameters': {
            'json_path': file_path,
            'doc_id': doc_id,
            'neo4j_uri': neo4j_uri,
            'neo4j_user': neo4j_user,
            'neo4j_password': neo4j_password,
            'neo4j_database': neo4j_database,
            'max_content_length': max_content_length
        },
        'reason': f'Upload {basename}'
    })

result = {
    '__task_result__': True,
    'output': {
        'files_to_upload': len(file_paths),
        'files': [os.path.basename(f) for f in file_paths]
    },
    'variables': {
        'batch_upload_started': True,
        'total_files': len(file_paths)
    },
    'decisions': [
        f"Queued {len(file_paths)} files for upload to Neo4j jsongraph"
    ],
    'push_tasks': push_tasks
}

print(json.dumps(result))
