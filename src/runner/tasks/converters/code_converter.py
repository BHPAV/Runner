import os
import json
import sys
import re
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
ext = os.path.splitext(source_path)[1].lower()

# Determine source type
if ext == '.ts' or ext == '.tsx':
    source_type = 'typescript'
elif ext == '.js' or ext == '.jsx':
    source_type = 'javascript'
else:
    source_type = 'code'

print(f"Converting {source_type}: {source_path}", file=sys.stderr)


def extract_code_structure(content, source_type):
    """Extract functions, classes, and imports using regex for JS/TS."""
    functions = []
    classes = []
    imports = []

    # Extract imports
    # ES6 imports: import X from 'Y', import { X } from 'Y', import * as X from 'Y'
    import_pattern = r"import\s+(?:(?:\*\s+as\s+(\w+))|(?:\{([^}]+)\})|(\w+))?\s*(?:,\s*(?:\{([^}]+)\}|(\w+)))?\s*from\s*['\"]([^'\"]+)['\"]"
    for match in re.finditer(import_pattern, content):
        module = match.group(6)
        imports.append({
            'type': 'import',
            'module': module,
            'raw': match.group(0)[:100]
        })

    # CommonJS require: const X = require('Y')
    require_pattern = r"(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
    for match in re.finditer(require_pattern, content):
        imports.append({
            'type': 'require',
            'module': match.group(2),
            'name': match.group(1)
        })

    # Extract functions
    # Regular functions: function name(args) { or async function name(args) {
    func_pattern = r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)"
    for match in re.finditer(func_pattern, content):
        functions.append({
            'name': match.group(1),
            'args': match.group(2).strip(),
            'type': 'function'
        })

    # Arrow functions: const name = (args) => or const name = async (args) =>
    arrow_pattern = r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>"
    for match in re.finditer(arrow_pattern, content):
        functions.append({
            'name': match.group(1),
            'type': 'arrow'
        })

    # Method definitions in objects/classes: name(args) { or async name(args) {
    method_pattern = r"^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{"
    for match in re.finditer(method_pattern, content, re.MULTILINE):
        name = match.group(1)
        if name not in ['if', 'for', 'while', 'switch', 'catch', 'function']:
            functions.append({
                'name': name,
                'type': 'method'
            })

    # Extract classes
    # class Name extends Base { or class Name implements Interface {
    class_pattern = r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?"
    for match in re.finditer(class_pattern, content):
        class_info = {
            'name': match.group(1),
            'extends': match.group(2),
            'implements': match.group(3).strip() if match.group(3) else None
        }
        classes.append(class_info)

    # TypeScript interfaces
    if source_type == 'typescript':
        interface_pattern = r"(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+([^{]+))?"
        for match in re.finditer(interface_pattern, content):
            classes.append({
                'name': match.group(1),
                'type': 'interface',
                'extends': match.group(2).strip() if match.group(2) else None
            })

        # TypeScript type aliases
        type_pattern = r"(?:export\s+)?type\s+(\w+)\s*="
        for match in re.finditer(type_pattern, content):
            classes.append({
                'name': match.group(1),
                'type': 'type_alias'
            })

    # Deduplicate functions by name
    seen_funcs = set()
    unique_functions = []
    for f in functions:
        if f['name'] not in seen_funcs:
            seen_funcs.add(f['name'])
            unique_functions.append(f)

    return unique_functions, classes, imports


try:
    with open(source_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    functions, classes, imports = extract_code_structure(content, source_type)

    # Build JSON structure
    json_data = {
        'source_file': source_path,
        'source_type': source_type,
        'content': content[:max_content_length] if len(content) > max_content_length else content,
        'functions': functions[:50],
        'classes': classes[:50],
        'imports': imports[:100]
    }

    if len(content) > max_content_length:
        json_data['truncated'] = True

    # Check total size
    json_str = json.dumps(json_data)
    if len(json_str) > max_content_length * 10:
        json_data['content'] = content[:max_content_length // 2]
        json_data['functions'] = functions[:20]
        json_data['classes'] = classes[:20]
        json_data['imports'] = imports[:30]
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
        'reason': f'Upload converted {source_type}: {basename}'
    }]

    result = {
        '__task_result__': True,
        'output': {
            'converted': source_path,
            'doc_id': doc_id,
            'source_type': source_type,
            'functions_count': len(functions),
            'classes_count': len(classes),
            'imports_count': len(imports)
        },
        'variables': {f'converted_{doc_id}': True},
        'decisions': [f'Converted {source_type}: {len(functions)} functions, {len(classes)} classes, {len(imports)} imports'],
        'push_tasks': push_tasks
    }

except Exception as e:
    result = {
        '__task_result__': True,
        'output': {'error': str(e), 'source_path': source_path},
        'errors': [f'{source_type} conversion failed: {str(e)}'],
        'abort': False
    }

print(json.dumps(result))
