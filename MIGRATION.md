# Migration Guide

This document describes the project reorganization from flat structure to Python package.

## Status: COMPLETE

The migration has been completed successfully:
- [x] Code duplication removed (compute_content_hash centralized in `runner.utils.hashing`)
- [x] CLI entry point created (`runner.cli:main`)
- [x] Module exports populated in `__init__.py` files
- [x] Test coverage added for core and tasks modules (116 tests passing)
- [x] All imports verified working

## Overview

The project has been reorganized from a flat directory structure to a proper Python package under `src/runner/`.

## Import Changes

### Utils

**Old:**
```python
# In each file, duplicated:
def compute_content_hash(kind, key, value):
    ...
```

**New:**
```python
from runner.utils.hashing import compute_content_hash, compute_merkle_hash
from runner.utils.neo4j import get_config, get_driver
```

### Hybridgraph

**Old:**
```python
from hybridgraph_queries import HybridGraphQuery
import sync_to_hybrid_task
import read_from_hybrid
import delete_source_task
import garbage_collect_task
import hybridgraph_health_task
```

**New:**
```python
from runner.hybridgraph.queries import HybridGraphQuery
from runner.hybridgraph import sync, reader, health, delete, gc, migrate
```

### Tasks

**Old:**
```python
import csv_to_json_task
import xml_to_json_task
import upload_dual_task
import upload_to_jsongraph_task
```

**New:**
```python
from runner.tasks.converters import csv, xml, yaml, markdown, text, code, python_ast, batch
from runner.tasks.upload import dual, jsongraph, batch
from runner.tasks.utilities import find_unrecorded_files, find_unrecorded_json
```

### Core

**Old:**
```python
import stack_runner
import runner
import bootstrap
```

**New:**
```python
from runner.core import stack_runner, runner, bootstrap
```

## File Location Changes

| Original | New Location |
|----------|--------------|
| `stack_runner.py` | `src/runner/core/stack_runner.py` |
| `runner.py` | `src/runner/core/runner.py` |
| `bootstrap.py` | `src/runner/core/bootstrap.py` |
| `csv_to_json_task.py` | `src/runner/tasks/converters/csv.py` |
| `xml_to_json_task.py` | `src/runner/tasks/converters/xml.py` |
| `yaml_to_json_task.py` | `src/runner/tasks/converters/yaml.py` |
| `markdown_to_json_task.py` | `src/runner/tasks/converters/markdown.py` |
| `text_to_json_task.py` | `src/runner/tasks/converters/text.py` |
| `code_to_json_task.py` | `src/runner/tasks/converters/code.py` |
| `python_ast_to_json_task.py` | `src/runner/tasks/converters/python_ast.py` |
| `batch_convert_task.py` | `src/runner/tasks/converters/batch.py` |
| `upload_dual_task.py` | `src/runner/tasks/upload/dual.py` |
| `upload_to_jsongraph_task.py` | `src/runner/tasks/upload/jsongraph.py` |
| `batch_upload_dual_task.py` | `src/runner/tasks/upload/batch.py` |
| `find_unrecorded_files_task.py` | `src/runner/tasks/utilities/find_unrecorded_files.py` |
| `find_unrecorded_json_task.py` | `src/runner/tasks/utilities/find_unrecorded_json.py` |
| `hybridgraph_queries.py` | `src/runner/hybridgraph/queries.py` |
| `sync_to_hybrid_task.py` | `src/runner/hybridgraph/sync.py` |
| `read_from_hybrid.py` | `src/runner/hybridgraph/reader.py` |
| `hybridgraph_health_task.py` | `src/runner/hybridgraph/health.py` |
| `delete_source_task.py` | `src/runner/hybridgraph/delete.py` |
| `garbage_collect_task.py` | `src/runner/hybridgraph/gc.py` |
| `migrate_to_hybrid.py` | `src/runner/hybridgraph/migrate.py` |

## Command Changes

### Running Tasks

**Old:**
```bash
python stack_runner.py -v start upload_dual --params '{"json_path": "file.json"}'
python bootstrap.py --seed
```

**New:**
```bash
python -m runner.core.stack_runner -v start upload_dual --params '{"json_path": "file.json"}'
python -m runner.core.bootstrap --seed
```

### Hybridgraph Operations

**Old:**
```bash
python sync_to_hybrid_task.py
python read_from_hybrid.py get <source_id>
python delete_source_task.py <source_id>
python garbage_collect_task.py
python hybridgraph_health_task.py
python migrate_to_hybrid.py --dry-run
```

**New (via unified CLI):**
```bash
runner sync
runner reader get <source_id>
runner delete <source_id>
runner gc
runner health
runner migrate --dry-run
```

**Or via module:**
```bash
python -m runner.cli sync
python -m runner.hybridgraph.sync
python -m runner.hybridgraph.reader get <source_id>
python -m runner.hybridgraph.delete <source_id>
python -m runner.hybridgraph.gc
python -m runner.hybridgraph.health
python -m runner.hybridgraph.migrate --dry-run
```

### Using as Library

**New capability:**
```python
# Import and use as a library
from runner.utils.hashing import compute_content_hash
from runner.hybridgraph.queries import HybridGraphQuery
from runner.hybridgraph.reader import HybridGraphReader

# Query hybridgraph
query = HybridGraphQuery()
results = query.find_by_content("example_key", "example_value")

# Read document
reader = HybridGraphReader()
doc = reader.get_document("source_id_123")
```

## Installation

```bash
# Development installation (editable mode)
pip install -e .

# With dev dependencies (pytest, mypy, ruff)
pip install -e ".[dev]"

# Production installation
pip install .
```

## Testing

**New:**
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_hashing.py

# Verbose output
pytest tests/ -v

# Coverage report
pytest tests/ --cov=runner --cov-report=html
```

## Package Benefits

1. **Proper imports**: No more relative imports or sys.path manipulation
2. **Namespace organization**: Clear separation of core, tasks, hybridgraph, and utils
3. **Reusable code**: Import utilities across multiple scripts without duplication
4. **Testing**: Proper test structure with pytest
5. **Distribution**: Can be installed via pip and used in other projects
6. **Type checking**: Better IDE support and mypy integration

## Backward Compatibility

The original files in the root directory are preserved and continue to work for backward compatibility. They import from the new package structure internally.

**Note:** The standalone scripts in the root will be deprecated in a future release. Please migrate to using the package imports.

## Migration Checklist

Package reorganization completed:
- [x] Package structure created under `src/runner/`
- [x] CLI entry point: `runner` command via `runner.cli:main`
- [x] Stack runner entry point: `stack-runner` command via `runner.core.stack_runner:main`
- [x] Centralized hashing utilities in `runner.utils.hashing`
- [x] Test suite: 116 tests passing
- [x] Module exports in `__init__.py` files

User migration steps:
- [ ] Install package: `pip install -e .`
- [ ] Update imports in custom scripts
- [ ] Update any scripts that call the old commands
- [ ] Test functionality with new imports
- [ ] Update documentation references
- [ ] Update CI/CD pipelines if applicable
- [ ] Remove old scripts after verifying new package works

## Questions?

See [CLAUDE.md](CLAUDE.md) for updated quick reference or [docs/README.md](docs/README.md) for detailed documentation.
