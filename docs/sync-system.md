# Sync System

The sync system keeps `jsongraph` and `hybridgraph` databases in sync.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       SYNC ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐                                                 │
│  │  New Data   │                                                 │
│  └──────┬──────┘                                                 │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              upload_dual_task.py                             ││
│  │         (Real-time dual write)                               ││
│  └─────────────┬─────────────────────────┬─────────────────────┘│
│                │                         │                       │
│                ▼                         ▼                       │
│  ┌─────────────────────┐   ┌─────────────────────┐              │
│  │     jsongraph       │   │    hybridgraph      │              │
│  │   (flat storage)    │   │   (deduplicated)    │              │
│  │                     │   │                     │              │
│  │  sync_status: null  │   │                     │              │
│  │  sync_status: synced│──▶│    ✓ In sync        │              │
│  └─────────────────────┘   └─────────────────────┘              │
│                │                         ▲                       │
│                │                         │                       │
│                └──── sync_to_hybrid ─────┘                       │
│                    (Incremental sync)                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Sync Methods

### 1. Real-Time Dual Write (Recommended)

Writes to both databases simultaneously:

```bash
# Single file
python stack_runner.py -v start upload_dual \
  --params '{"json_path": "data.json"}'

# Batch files
python stack_runner.py -v start batch_upload_dual \
  --params '{"file_paths": ["file1.json", "file2.json"]}'
```

**Pros:**
- Immediate consistency
- No sync delay
- Atomic operation

**Cons:**
- Slightly slower uploads
- Both databases must be available

### 2. Incremental Sync

Syncs unsynced documents from jsongraph to hybridgraph:

```bash
# Direct execution
python sync_to_hybrid_task.py --limit 100

# Via stack runner
python stack_runner.py -v start sync_to_hybrid --params '{"limit": 50}'
```

**Pros:**
- Works with existing data
- Handles offline scenarios
- Batch processing

**Cons:**
- Small delay between upload and sync
- Requires periodic execution

### 3. Continuous Background Sync

Self-rescheduling periodic sync:

```bash
python stack_runner.py -v start periodic_sync \
  --params '{"continuous": true, "interval_seconds": 60}'
```

## Sync Tracking

Documents in jsongraph have a `sync_status` property:

| Status | Meaning |
|--------|---------|
| `null` | Never synced (legacy data) |
| `pending` | Awaiting sync |
| `synced` | Successfully synced to hybridgraph |

## Scripts

### sync_to_hybrid_task.py

Incremental sync script:

```bash
# Sync up to 100 documents
python sync_to_hybrid_task.py --limit 100

# Quiet mode (for cron)
python sync_to_hybrid_task.py --limit 100 --quiet
```

**Process:**
1. Find documents with `sync_status IS NULL OR sync_status = 'pending'`
2. Compute content and Merkle hashes
3. MERGE into hybridgraph (creating/reusing nodes)
4. Update `sync_status = 'synced'` in jsongraph

### setup_auto_sync.py

Configure automatic sync:

```bash
# Setup all methods
python setup_auto_sync.py --method all --interval 60

# Task-based only
python setup_auto_sync.py --method task

# Check status
python setup_auto_sync.py --method status
```

### migrate_to_hybrid.py

Full migration (initial setup):

```bash
python migrate_to_hybrid.py --source-db jsongraph --target-db hybridgraph
```

## Cron Setup

For cron-based sync, use the generated script:

```bash
# Generate cron script
python setup_auto_sync.py --method cron

# Add to crontab (runs every minute)
crontab -e
* * * * * /path/to/Runner/sync_cron.sh
```

## Monitoring

### Check Sync Status

```bash
python setup_auto_sync.py --method status
```

### Query Unsynced Documents

```cypher
-- In jsongraph
MATCH (d:Data)
WHERE d.sync_status IS NULL OR d.sync_status = 'pending'
RETURN d.doc_id, count(*) AS nodes
ORDER BY nodes DESC
```

### Check Last Sync Times

```cypher
-- In hybridgraph
MATCH (s:Source)
RETURN s.source_id, s.last_synced
ORDER BY s.last_synced DESC
LIMIT 10
```

## Error Handling

The sync system handles:

- **Missing documents**: Skips and logs error
- **Invalid JSON**: Reports and continues
- **Connection failures**: Retries on next run
- **Duplicate content**: Safely merges (increments ref_count)

Errors are collected in the task output:

```json
{
  "documents_synced": 45,
  "content_created": 14,
  "errors": ["doc_xyz: Connection timeout"]
}
```

## Best Practices

1. **For new projects**: Use `upload_dual` for all uploads
2. **For existing data**: Run `migrate_to_hybrid.py` once, then use dual uploads
3. **For batch imports**: Use `batch_upload_dual` or run sync after
4. **For reliability**: Set up periodic sync as a safety net
