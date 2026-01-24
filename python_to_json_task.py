import os
import json
import sys
import ast
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

print(f"Converting Python: {source_path}", file=sys.stderr)


def extract_python_structure(content):
    """Extract functions, classes, and imports using AST."""
    functions = []
    classes = []
    imports = []

    try:
        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                func_info = {
                    'name': node.name,
                    'lineno': node.lineno,
                    'args': [arg.arg for arg in node.args.args],
                    'decorators': [
                        ast.unparse(d) if hasattr(ast, 'unparse') else str(d)
                        for d in node.decorator_list
                    ],
                    'is_async': isinstance(node, ast.AsyncFunctionDef),
                    'docstring': ast.get_docstring(node) or ''
                }
                functions.append(func_info)

            elif isinstance(node, ast.ClassDef):
                # Get base classes
                bases = []
                for base in node.bases:
                    if hasattr(ast, 'unparse'):
                        bases.append(ast.unparse(base))
                    elif isinstance(base, ast.Name):
                        bases.append(base.id)
                    else:
                        bases.append(str(base))

                # Get methods
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(item.name)

                class_info = {
                    'name': node.name,
                    'lineno': node.lineno,
                    'bases': bases,
                    'methods': methods,
                    'decorators': [
                        ast.unparse(d) if hasattr(ast, 'unparse') else str(d)
                        for d in node.decorator_list
                    ],
                    'docstring': ast.get_docstring(node) or ''
                }
                classes.append(class_info)

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        'type': 'import',
                        'module': alias.name,
                        'alias': alias.asname
                    })

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    imports.append({
                        'type': 'from',
                        'module': module,
                        'name': alias.name,
                        'alias': alias.asname
                    })

    except SyntaxError as e:
        # If AST parsing fails, return partial results with error
        return functions, classes, imports, str(e)

    return functions, classes, imports, None


try:
    with open(source_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    functions, classes, imports, parse_error = extract_python_structure(content)

    # Build JSON structure
    json_data = {
        'source_file': source_path,
        'source_type': 'python',
        'content': content[:max_content_length] if len(content) > max_content_length else content,
        'functions': functions[:50],  # Limit
        'classes': classes[:50],
        'imports': imports[:100]
    }

    if len(content) > max_content_length:
        json_data['truncated'] = True

    if parse_error:
        json_data['parse_error'] = parse_error

    # Check total size
    json_str = json.dumps(json_data)
    if len(json_str) > max_content_length * 10:
        # Reduce content
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
        'reason': f'Upload converted Python: {basename}'
    }]

    result = {
        '__task_result__': True,
        'output': {
            'converted': source_path,
            'doc_id': doc_id,
            'functions_count': len(functions),
            'classes_count': len(classes),
            'imports_count': len(imports),
            'parse_error': parse_error
        },
        'variables': {f'converted_{doc_id}': True},
        'decisions': [f'Converted Python: {len(functions)} functions, {len(classes)} classes, {len(imports)} imports'],
        'push_tasks': push_tasks
    }

except Exception as e:
    result = {
        '__task_result__': True,
        'output': {'error': str(e), 'source_path': source_path},
        'errors': [f'Python conversion failed: {str(e)}'],
        'abort': False
    }

print(json.dumps(result))
