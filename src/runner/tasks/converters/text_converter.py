import os
import json
import sys
from pathlib import Path

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Parameters
source_path = params.get('source_path')
neo4j_uri = params.get('neo4j_uri', os.environ.get('NEO4J_URI', 'bolt://localhost:7687'))
neo4j_user = params.get('neo4j_user', os.environ.get('NEO4J_USER', 'neo4j'))
neo4j_password = params.get('neo4j_password', os.environ.get('NEO4J_PASSWORD', ''))
neo4j_database = params.get('neo4j_database', os.environ.get('NEO4J_DATABASE', 'neo4j'))
max_content_length = params.get('max_content_length', 5000)

if not source_path:
    result = {
        '__task_result__': True,
        'output': {'error': 'No source_path provided'},
        'errors': ['source_path parameter is required'],
        'abort': True
    }
    print(json.dumps(result))
    sys.exit(0)

source_path = os.path.expanduser(source_path)

if not os.path.exists(source_path):
    result = {
        '__task_result__': True,
        'output': {'error': f'File not found: {source_path}'},
        'errors': [f'File not found: {source_path}'],
        'abort': True
    }
    print(json.dumps(result))
    sys.exit(0)

# Generate doc_id from filename (include extension to avoid collisions)
basename = os.path.basename(source_path)
doc_id = basename.replace('.', '_').replace(' ', '_')

print(f"Converting text file: {source_path}", file=sys.stderr)

try:
    with open(source_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    lines = content.split('\n')
    words = content.split()

    # Build JSON structure
    json_data = {
        'source_file': source_path,
        'source_type': 'text',
        'content': content[:max_content_length] if len(content) > max_content_length else content,
        'line_count': len(lines),
        'word_count': len(words),
        'char_count': len(content)
    }

    if len(content) > max_content_length:
        json_data['truncated'] = True

    # Push to upload_jsongraph
    push_tasks = [{
        'task_id': 'upload_jsongraph',
        'parameters': {
            'json_data': json_data,
            'doc_id': doc_id,
            'neo4j_uri': neo4j_uri,
            'neo4j_user': neo4j_user,
            'neo4j_password': neo4j_password,
            'neo4j_database': neo4j_database,
            'max_content_length': max_content_length
        },
        'reason': f'Upload converted text: {basename}'
    }]

    result = {
        '__task_result__': True,
        'output': {
            'converted': source_path,
            'doc_id': doc_id,
            'line_count': len(lines),
            'word_count': len(words)
        },
        'variables': {f'converted_{doc_id}': True},
        'decisions': [f'Converted text file: {len(lines)} lines, {len(words)} words'],
        'push_tasks': push_tasks
    }

except Exception as e:
    result = {
        '__task_result__': True,
        'output': {'error': str(e), 'source_path': source_path},
        'errors': [f'Text conversion failed: {str(e)}'],
        'abort': False
    }

print(json.dumps(result))
