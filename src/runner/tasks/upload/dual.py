#!/usr/bin/env python3
"""
Dual-write upload task: writes to both jsongraph (flat) and hybridgraph (deduplicated).

This ensures real-time sync between the two databases when uploading new JSON data.

Usage:
  - As stack runner task: registered as 'upload_dual'
  - Direct: python upload_dual_task.py (with TASK_PARAMS env var)

Parameters:
  json_path: Path to JSON file to upload
  json_data: Direct JSON object (alternative to json_path)
  doc_id: Document identifier (auto-generated if not provided)
"""

import os
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

try:
    from runner.utils.hashing import compute_content_hash, compute_merkle_hash
except ImportError:
    # Fallback for direct execution outside package
    import hashlib
    def compute_content_hash(kind: str, key: str, value: str) -> str:
        content = f"{kind}|{key}|{kind}:{value}"
        return "c:" + hashlib.sha256(content.encode()).hexdigest()[:32]

    def compute_merkle_hash(kind: str, key: str, child_hashes: list) -> str:
        sorted_children = "|".join(sorted(child_hashes))
        content = f"{kind}|{key}|{sorted_children}"
        return "m:" + hashlib.sha256(content.encode()).hexdigest()[:32]

params = json.loads(os.environ.get('TASK_PARAMS', '{}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{}'))

# Parameters
json_path = params.get('json_path')
json_data = params.get('json_data')
doc_id = params.get('doc_id')
neo4j_uri = params.get('neo4j_uri', os.environ.get('NEO4J_URI', 'bolt://localhost:7687'))
neo4j_user = params.get('neo4j_user', os.environ.get('NEO4J_USER', 'neo4j'))
neo4j_password = params.get('neo4j_password', os.environ.get('NEO4J_PASSWORD', ''))
source_db = params.get('source_db', os.environ.get('SOURCE_DB', 'jsongraph'))
target_db = params.get('target_db', os.environ.get('TARGET_DB', 'hybridgraph'))
max_content_length = params.get('max_content_length', 5000)

try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

# Load JSON data
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
        basename = os.path.basename(json_path)
        doc_id = basename.replace('.', '_').replace(' ', '_')
    else:
        data_str = json.dumps(data, sort_keys=True)
        doc_id = hashlib.md5(data_str.encode()).hexdigest()[:12]

print(f"Dual upload: {source_info}", file=sys.stderr)
print(f"Doc ID: {doc_id}", file=sys.stderr)
print(f"Source DB: {source_db}, Target DB: {target_db}", file=sys.stderr)


def flatten_json(data, parent_path="/root", parent_key="root"):
    """Flatten JSON into nodes with computed hashes."""
    nodes = []

    if isinstance(data, dict):
        child_hashes = []
        children = []

        for key, value in data.items():
            child_path = f"{parent_path}/{key}"
            child_nodes = flatten_json(value, child_path, key)
            children.extend(child_nodes)
            if child_nodes:
                child_hashes.append(child_nodes[0]['hash'])

        merkle = compute_merkle_hash('object', parent_key, child_hashes)
        node = {
            'path': parent_path,
            'kind': 'object',
            'key': parent_key,
            'value_str': None,
            'value_num': None,
            'value_bool': None,
            'hash': merkle,
            'child_keys': sorted(data.keys()),
        }
        nodes.append(node)
        nodes.extend(children)

    elif isinstance(data, list):
        child_hashes = []
        children = []

        for idx, value in enumerate(data):
            child_path = f"{parent_path}/{idx}"
            child_nodes = flatten_json(value, child_path, str(idx))
            children.extend(child_nodes)
            if child_nodes:
                child_hashes.append(child_nodes[0]['hash'])

        merkle = compute_merkle_hash('array', parent_key, child_hashes)
        node = {
            'path': parent_path,
            'kind': 'array',
            'key': parent_key,
            'value_str': None,
            'value_num': None,
            'value_bool': None,
            'hash': merkle,
            'child_keys': [],
        }
        nodes.append(node)
        nodes.extend(children)

    elif isinstance(data, bool):
        value_str = str(data).lower()
        node = {
            'path': parent_path,
            'kind': 'boolean',
            'key': parent_key,
            'value_str': value_str,
            'value_num': None,
            'value_bool': data,
            'hash': compute_content_hash('boolean', parent_key, value_str),
            'child_keys': None,
        }
        nodes.append(node)

    elif isinstance(data, (int, float)):
        value_str = str(data)
        node = {
            'path': parent_path,
            'kind': 'number',
            'key': parent_key,
            'value_str': value_str,
            'value_num': data,
            'value_bool': None,
            'hash': compute_content_hash('number', parent_key, value_str),
            'child_keys': None,
        }
        nodes.append(node)

    elif isinstance(data, str):
        truncated = data[:max_content_length] if len(data) > max_content_length else data
        node = {
            'path': parent_path,
            'kind': 'string',
            'key': parent_key,
            'value_str': truncated,
            'value_num': None,
            'value_bool': None,
            'hash': compute_content_hash('string', parent_key, truncated),
            'child_keys': None,
        }
        nodes.append(node)

    elif data is None:
        node = {
            'path': parent_path,
            'kind': 'null',
            'key': parent_key,
            'value_str': None,
            'value_num': None,
            'value_bool': None,
            'hash': compute_content_hash('null', parent_key, 'null'),
            'child_keys': None,
        }
        nodes.append(node)

    return nodes


def upload_to_jsongraph(driver, database, doc_id, nodes):
    """Upload to jsongraph (flat Data nodes)."""
    nodes_created = 0
    rels_created = 0

    with driver.session(database=database) as session:
        # Delete existing
        session.run("MATCH (d:Data {doc_id: $doc_id}) DETACH DELETE d", doc_id=doc_id)

        # Create nodes
        for node in nodes:
            session.run("""
                CREATE (d:Data {
                    doc_id: $doc_id,
                    path: $path,
                    kind: $kind,
                    key: $key,
                    value_str: $value_str,
                    value_num: $value_num,
                    value_bool: $value_bool,
                    sync_status: 'synced'
                })
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
        for node in nodes:
            if node['kind'] in ['object', 'array']:
                parent_path = node['path']
                # Find children
                for child in nodes:
                    if child['path'].startswith(parent_path + '/'):
                        # Check it's a direct child
                        relative = child['path'][len(parent_path)+1:]
                        if '/' not in relative:
                            session.run("""
                                MATCH (parent:Data {doc_id: $doc_id, path: $parent_path})
                                MATCH (child:Data {doc_id: $doc_id, path: $child_path})
                                CREATE (parent)-[:CONTAINS]->(child)
                            """,
                                doc_id=doc_id,
                                parent_path=parent_path,
                                child_path=child['path']
                            )
                            rels_created += 1

    return nodes_created, rels_created


def upload_to_hybridgraph(driver, database, doc_id, nodes):
    """Upload to hybridgraph (deduplicated Content/Structure nodes)."""
    content_created = 0
    structure_created = 0
    now = datetime.now(timezone.utc).isoformat()

    with driver.session(database=database) as session:
        # Build lookup maps
        node_by_path = {n['path']: n for n in nodes}

        # 1. Merge Content nodes (leaves)
        content_nodes = [n for n in nodes if n['kind'] in ['string', 'number', 'boolean', 'null']]
        for node in content_nodes:
            result = session.run("""
                MERGE (c:Content {hash: $hash})
                ON CREATE SET c.kind = $kind, c.key = $key,
                              c.value_str = $value_str, c.value_num = $value_num,
                              c.value_bool = $value_bool, c.ref_count = 1
                ON MATCH SET c.ref_count = c.ref_count + 1
                RETURN c.ref_count = 1 AS is_new
            """,
                hash=node['hash'],
                kind=node['kind'],
                key=node['key'],
                value_str=node['value_str'],
                value_num=node['value_num'],
                value_bool=node['value_bool']
            )
            if result.single()['is_new']:
                content_created += 1

        # 2. Merge Structure nodes (containers)
        structure_nodes = [n for n in nodes if n['kind'] in ['object', 'array']]
        for node in structure_nodes:
            result = session.run("""
                MERGE (s:Structure {merkle: $merkle})
                ON CREATE SET s.kind = $kind, s.key = $key,
                              s.child_keys = $child_keys, s.ref_count = 1
                ON MATCH SET s.ref_count = s.ref_count + 1
                RETURN s.ref_count = 1 AS is_new
            """,
                merkle=node['hash'],
                kind=node['kind'],
                key=node['key'],
                child_keys=node['child_keys']
            )
            if result.single()['is_new']:
                structure_created += 1

        # 3. Create CONTAINS relationships between structures
        for node in structure_nodes:
            parent_path = node['path']
            for child in nodes:
                if child['path'].startswith(parent_path + '/'):
                    relative = child['path'][len(parent_path)+1:]
                    if '/' not in relative and child['kind'] in ['object', 'array']:
                        session.run("""
                            MATCH (parent:Structure {merkle: $parent_merkle})
                            MATCH (child:Structure {merkle: $child_merkle})
                            MERGE (parent)-[:CONTAINS {key: $key}]->(child)
                        """,
                            parent_merkle=node['hash'],
                            child_merkle=child['hash'],
                            key=child['key']
                        )

        # 4. Create HAS_VALUE relationships to content
        for node in structure_nodes:
            parent_path = node['path']
            for child in nodes:
                if child['path'].startswith(parent_path + '/'):
                    relative = child['path'][len(parent_path)+1:]
                    if '/' not in relative and child['kind'] in ['string', 'number', 'boolean', 'null']:
                        session.run("""
                            MATCH (s:Structure {merkle: $structure_merkle})
                            MATCH (c:Content {hash: $content_hash})
                            MERGE (s)-[:HAS_VALUE {key: $key}]->(c)
                        """,
                            structure_merkle=node['hash'],
                            content_hash=child['hash'],
                            key=child['key']
                        )

        # 5. Create/update Source node
        root_node = node_by_path.get('/root')
        if root_node:
            session.run("""
                MERGE (source:Source {source_id: $doc_id})
                SET source.source_type = 'document',
                    source.name = $doc_id,
                    source.node_count = $node_count,
                    source.last_updated = $now
                WITH source
                MATCH (root:Structure {merkle: $root_merkle})
                MERGE (source)-[:HAS_ROOT]->(root)
            """,
                doc_id=doc_id,
                node_count=len(nodes),
                now=now,
                root_merkle=root_node['hash']
            )

    return content_created, structure_created


# Main execution
if not HAS_NEO4J:
    result = {
        '__task_result__': True,
        'output': {'error': 'neo4j driver not installed'},
        'errors': ['neo4j Python driver not installed'],
        'abort': False
    }
    print(json.dumps(result))
    sys.exit(0)

try:
    # Flatten JSON with hashes
    nodes = flatten_json(data)
    print(f"Flattened into {len(nodes)} nodes", file=sys.stderr)

    # Connect
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    driver.verify_connectivity()

    # Upload to both databases
    jsongraph_nodes, jsongraph_rels = upload_to_jsongraph(driver, source_db, doc_id, nodes)
    print(f"jsongraph: {jsongraph_nodes} nodes, {jsongraph_rels} relationships", file=sys.stderr)

    content_created, structure_created = upload_to_hybridgraph(driver, target_db, doc_id, nodes)
    print(f"hybridgraph: +{content_created} content, +{structure_created} structure", file=sys.stderr)

    driver.close()

    task_result = {
        '__task_result__': True,
        'output': {
            'success': True,
            'doc_id': doc_id,
            'source': json_path or 'json_data',
            'jsongraph': {
                'nodes_created': jsongraph_nodes,
                'relationships_created': jsongraph_rels,
            },
            'hybridgraph': {
                'content_created': content_created,
                'structure_created': structure_created,
            },
        },
        'variables': {
            f'uploaded_{doc_id}': True,
            'last_upload_doc_id': doc_id,
        },
        'decisions': [
            f"Dual upload: {doc_id}",
            f"jsongraph: {jsongraph_nodes} nodes",
            f"hybridgraph: +{content_created} content, +{structure_created} structure",
        ]
    }

except Exception as e:
    task_result = {
        '__task_result__': True,
        'output': {
            'success': False,
            'doc_id': doc_id,
            'error': str(e)
        },
        'errors': [str(e)]
    }

print(json.dumps(task_result))
