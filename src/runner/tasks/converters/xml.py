import os
import json
import sys
import xml.etree.ElementTree as ET
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

print(f"Converting XML: {source_path}", file=sys.stderr)


def element_to_dict(element, max_depth=10, current_depth=0):
    """Recursively convert XML element to dictionary."""
    if current_depth >= max_depth:
        return {'_truncated': True, '_text': element.text or ''}

    result = {}

    # Add attributes
    if element.attrib:
        result['@attributes'] = dict(element.attrib)

    # Add text content
    if element.text and element.text.strip():
        result['@text'] = element.text.strip()

    # Add children
    children = {}
    for child in element:
        child_data = element_to_dict(child, max_depth, current_depth + 1)
        tag = child.tag

        # Handle namespace
        if '}' in tag:
            tag = tag.split('}', 1)[1]

        if tag in children:
            # Convert to list if multiple same-named children
            if not isinstance(children[tag], list):
                children[tag] = [children[tag]]
            children[tag].append(child_data)
        else:
            children[tag] = child_data

    if children:
        result['@children'] = children

    # Add tail text if present
    if element.tail and element.tail.strip():
        result['@tail'] = element.tail.strip()

    return result


try:
    tree = ET.parse(source_path)
    root = tree.getroot()

    # Get root tag (strip namespace if present)
    root_tag = root.tag
    if '}' in root_tag:
        root_tag = root_tag.split('}', 1)[1]

    # Convert to dict
    data = element_to_dict(root)

    # Build JSON structure
    json_data = {
        'source_file': source_path,
        'source_type': 'xml',
        'root_tag': root_tag,
        'data': data
    }

    # Check size and truncate if needed
    json_str = json.dumps(json_data, default=str)
    if len(json_str) > max_content_length * 10:
        # Re-parse with smaller depth
        data = element_to_dict(root, max_depth=3)
        json_data['data'] = data
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
        'reason': f'Upload converted XML: {basename}'
    }]

    result = {
        '__task_result__': True,
        'output': {
            'converted': source_path,
            'doc_id': doc_id,
            'root_tag': root_tag
        },
        'variables': {f'converted_{doc_id}': True},
        'decisions': [f'Converted XML with root tag: {root_tag}'],
        'push_tasks': push_tasks
    }

except ET.ParseError as e:
    result = {
        '__task_result__': True,
        'output': {'error': f'XML parse error: {str(e)}', 'source_path': source_path},
        'errors': [f'XML parse error: {str(e)}'],
        'abort': False
    }
except Exception as e:
    result = {
        '__task_result__': True,
        'output': {'error': str(e), 'source_path': source_path},
        'errors': [f'XML conversion failed: {str(e)}'],
        'abort': False
    }

print(json.dumps(result))
