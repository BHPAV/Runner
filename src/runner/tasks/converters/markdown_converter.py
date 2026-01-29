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

print(f"Converting Markdown: {source_path}", file=sys.stderr)


def parse_markdown(content):
    """Extract structure from markdown content."""
    sections = []
    code_blocks = []
    links = []

    # Extract code blocks first (to avoid parsing their contents)
    code_pattern = r'```(\w*)\n(.*?)```'
    for match in re.finditer(code_pattern, content, re.DOTALL):
        language = match.group(1) or 'text'
        code = match.group(2).strip()
        code_blocks.append({
            'language': language,
            'code': code[:max_content_length] if len(code) > max_content_length else code
        })

    # Remove code blocks for further parsing
    content_no_code = re.sub(code_pattern, '', content, flags=re.DOTALL)

    # Extract headers and create sections
    header_pattern = r'^(#{1,6})\s+(.+)$'
    current_section = {'level': 0, 'title': 'Document', 'content': []}

    for line in content_no_code.split('\n'):
        header_match = re.match(header_pattern, line)
        if header_match:
            # Save current section if it has content
            if current_section['content']:
                sections.append(current_section)

            level = len(header_match.group(1))
            title = header_match.group(2).strip()
            current_section = {'level': level, 'title': title, 'content': []}
        else:
            stripped = line.strip()
            if stripped:
                current_section['content'].append(stripped)

    # Don't forget the last section
    if current_section['content']:
        sections.append(current_section)

    # Extract links [text](url) and ![alt](url)
    link_pattern = r'!?\[([^\]]*)\]\(([^)]+)\)'
    for match in re.finditer(link_pattern, content_no_code):
        is_image = content_no_code[match.start()] == '!'
        links.append({
            'type': 'image' if is_image else 'link',
            'text': match.group(1),
            'url': match.group(2)
        })

    # Convert section content lists to strings
    for section in sections:
        section['content'] = '\n'.join(section['content'])

    return sections, code_blocks, links


try:
    with open(source_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    sections, code_blocks, links = parse_markdown(content)

    # Build JSON structure
    json_data = {
        'source_file': source_path,
        'source_type': 'markdown',
        'content': content[:max_content_length] if len(content) > max_content_length else content,
        'sections': sections[:50],  # Limit sections
        'code_blocks': code_blocks[:20],  # Limit code blocks
        'links': links[:50]  # Limit links
    }

    if len(content) > max_content_length:
        json_data['truncated'] = True

    # Check total size
    json_str = json.dumps(json_data)
    if len(json_str) > max_content_length * 10:
        # Further reduce
        json_data['sections'] = sections[:10]
        json_data['code_blocks'] = code_blocks[:5]
        json_data['links'] = links[:10]
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
        'reason': f'Upload converted Markdown: {basename}'
    }]

    result = {
        '__task_result__': True,
        'output': {
            'converted': source_path,
            'doc_id': doc_id,
            'sections_count': len(sections),
            'code_blocks_count': len(code_blocks),
            'links_count': len(links)
        },
        'variables': {f'converted_{doc_id}': True},
        'decisions': [f'Converted Markdown: {len(sections)} sections, {len(code_blocks)} code blocks, {len(links)} links'],
        'push_tasks': push_tasks
    }

except Exception as e:
    result = {
        '__task_result__': True,
        'output': {'error': str(e), 'source_path': source_path},
        'errors': [f'Markdown conversion failed: {str(e)}'],
        'abort': False
    }

print(json.dumps(result))
