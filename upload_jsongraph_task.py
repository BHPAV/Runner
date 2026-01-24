import os
import json
import sys
import hashlib
from pathlib import Path

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Parameters - support both json_path (file) and json_data (direct object)
json_path = params.get('json_path')
json_data = params.get('json_data')
doc_id = params.get('doc_id')
neo4j_uri = params.get('neo4j_uri', os.environ.get('NEO4J_URI', 'bolt://localhost:7687'))
neo4j_user = params.get('neo4j_user', os.environ.get('NEO4J_USER', 'neo4j'))
neo4j_password = params.get('neo4j_password', os.environ.get('NEO4J_PASSWORD', ''))
neo4j_database = params.get('neo4j_database', os.environ.get('NEO4J_DATABASE', 'neo4j'))
max_content_length = params.get('max_content_length', 5000)

# Try to import neo4j driver
try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

# Load JSON data from path or use direct data
data = None
source_info = ""

if json_data:
    data = json_data
    source_info = f"direct data (doc_id: {doc_id})"
elif json_path:
    json_path = os.path.expanduser(json_path)
    if not os.path.exists(json_path):
        result = {
            '__task_result__': True,
            'output': {'error': f'File not found: {json_path}'},
            'errors': [f'File not found: {json_path}'],
            'abort': True
        }
        print(json.dumps(result))
        sys.exit(0)

    try:
        with open(json_path, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
        source_info = f"file: {json_path}"
    except json.JSONDecodeError as e:
        result = {
            '__task_result__': True,
            'output': {'error': f'Invalid JSON: {str(e)}', 'path': json_path},
            'errors': [f'JSON parse error: {str(e)}'],
            'abort': True
        }
        print(json.dumps(result))
        sys.exit(0)
else:
    result = {
        '__task_result__': True,
        'output': {'error': 'No json_path or json_data provided'},
        'errors': ['Either json_path or json_data parameter is required'],
        'abort': True
    }
    print(json.dumps(result))
    sys.exit(0)

# Generate doc_id if not provided
if not doc_id:
    if json_path:
        # Include extension to avoid collisions
        basename = os.path.basename(json_path)
        doc_id = basename.replace('.', '_').replace(' ', '_')
    else:
        # Generate from data content
        data_str = json.dumps(data, sort_keys=True)
        doc_id = hashlib.md5(data_str.encode()).hexdigest()[:12]

print(f"Uploading to Neo4j jsongraph: {source_info}", file=sys.stderr)
print(f"Doc ID: {doc_id}", file=sys.stderr)
print(f"Neo4j URI: {neo4j_uri}", file=sys.stderr)


def flatten_json(data, parent_path="/root", parent_key="root"):
    """
    Flatten JSON into list of nodes for the jsongraph pattern.
    Each node has: path, kind, key, value_str, value_num, value_bool
    Returns list of (node_dict, parent_path) tuples
    """
    nodes = []

    if isinstance(data, dict):
        node = {
            'path': parent_path,
            'kind': 'object',
            'key': parent_key,
            'value_str': None,
            'value_num': None,
            'value_bool': None
        }
        nodes.append((node, None if parent_path == "/root" else "/".join(parent_path.split("/")[:-1]) or "/root"))

        for key, value in data.items():
            child_path = f"{parent_path}/{key}"
            nodes.extend(flatten_json(value, child_path, key))

    elif isinstance(data, list):
        node = {
            'path': parent_path,
            'kind': 'array',
            'key': parent_key,
            'value_str': None,
            'value_num': None,
            'value_bool': None
        }
        nodes.append((node, None if parent_path == "/root" else "/".join(parent_path.split("/")[:-1]) or "/root"))

        for idx, value in enumerate(data):
            child_path = f"{parent_path}/{idx}"
            nodes.extend(flatten_json(value, child_path, str(idx)))

    elif isinstance(data, bool):
        node = {
            'path': parent_path,
            'kind': 'boolean',
            'key': parent_key,
            'value_str': str(data).lower(),
            'value_num': None,
            'value_bool': data
        }
        nodes.append((node, "/".join(parent_path.split("/")[:-1]) or "/root"))

    elif isinstance(data, (int, float)):
        node = {
            'path': parent_path,
            'kind': 'number',
            'key': parent_key,
            'value_str': str(data),
            'value_num': data,
            'value_bool': None
        }
        nodes.append((node, "/".join(parent_path.split("/")[:-1]) or "/root"))

    elif isinstance(data, str):
        # Truncate long strings
        truncated = data[:max_content_length] if len(data) > max_content_length else data
        node = {
            'path': parent_path,
            'kind': 'string',
            'key': parent_key,
            'value_str': truncated,
            'value_num': None,
            'value_bool': None
        }
        nodes.append((node, "/".join(parent_path.split("/")[:-1]) or "/root"))

    elif data is None:
        node = {
            'path': parent_path,
            'kind': 'null',
            'key': parent_key,
            'value_str': None,
            'value_num': None,
            'value_bool': None
        }
        nodes.append((node, "/".join(parent_path.split("/")[:-1]) or "/root"))

    return nodes


def upload_to_neo4j(driver, database, doc_id, nodes):
    """Upload flattened JSON nodes to Neo4j."""
    nodes_created = 0
    relationships_created = 0

    with driver.session(database=database) as session:
        # Delete existing nodes for this doc_id
        session.run(
            "MATCH (d:Data {doc_id: $doc_id}) DETACH DELETE d",
            doc_id=doc_id
        )

        # Create all nodes first
        for node, parent_path in nodes:
            result = session.run(
                """
                CREATE (d:Data {
                    doc_id: $doc_id,
                    path: $path,
                    kind: $kind,
                    key: $key,
                    value_str: $value_str,
                    value_num: $value_num,
                    value_bool: $value_bool
                })
                RETURN d
                """,
                doc_id=doc_id,
                path=node['path'],
                kind=node['kind'],
                key=node['key'],
                value_str=node['value_str'],
                value_num=node['value_num'],
                value_bool=node['value_bool']
            )
            nodes_created += 1

        # Create relationships
        for node, parent_path in nodes:
            if parent_path:
                result = session.run(
                    """
                    MATCH (parent:Data {doc_id: $doc_id, path: $parent_path})
                    MATCH (child:Data {doc_id: $doc_id, path: $child_path})
                    CREATE (parent)-[:CONTAINS]->(child)
                    RETURN parent, child
                    """,
                    doc_id=doc_id,
                    parent_path=parent_path,
                    child_path=node['path']
                )
                relationships_created += 1

    return nodes_created, relationships_created


# Main execution
if not HAS_NEO4J:
    result = {
        '__task_result__': True,
        'output': {
            'error': 'neo4j Python driver not installed',
            'install_cmd': 'pip install neo4j'
        },
        'errors': ['neo4j Python driver not installed. Run: pip install neo4j'],
        'abort': False
    }
    print(json.dumps(result))
    sys.exit(0)

try:
    # Flatten the JSON data
    nodes = flatten_json(data)
    print(f"Flattened JSON into {len(nodes)} nodes", file=sys.stderr)

    # Connect and upload
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

    # Verify connectivity
    driver.verify_connectivity()
    print("Connected to Neo4j", file=sys.stderr)

    nodes_created, relationships_created = upload_to_neo4j(driver, neo4j_database, doc_id, nodes)

    driver.close()

    task_result = {
        '__task_result__': True,
        'output': {
            'success': True,
            'doc_id': doc_id,
            'source': json_path or 'json_data',
            'nodes_created': nodes_created,
            'relationships_created': relationships_created,
            'error': None
        },
        'variables': {
            f'uploaded_{doc_id}': True,
            'last_upload_doc_id': doc_id
        },
        'decisions': [
            f"Uploaded doc_id: {doc_id}",
            f"Nodes created: {nodes_created}, Relationships: {relationships_created}",
            "Status: SUCCESS"
        ]
    }

except Exception as e:
    task_result = {
        '__task_result__': True,
        'output': {
            'success': False,
            'doc_id': doc_id,
            'source': json_path or 'json_data',
            'nodes_created': 0,
            'relationships_created': 0,
            'error': str(e)
        },
        'variables': {
            f'uploaded_{doc_id}': False,
            'last_upload_doc_id': doc_id
        },
        'decisions': [
            f"Upload failed for doc_id: {doc_id}",
            f"Error: {str(e)}",
            "Status: FAILED"
        ],
        'errors': [str(e)]
    }

print(json.dumps(task_result))
