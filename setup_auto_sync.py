#!/usr/bin/env python3
"""
Setup automatic sync between jsongraph and hybridgraph.

Options:
1. APOC Periodic Job - runs inside Neo4j
2. Stack Runner Task - runs via external scheduler
3. Manual trigger - on-demand sync

Usage:
    python setup_auto_sync.py [--method apoc|task|both] [--interval 60]
"""

import argparse
import json
import os
import sqlite3
import sys

try:
    from neo4j import GraphDatabase
except ImportError:
    print("Error: neo4j driver not installed")
    sys.exit(1)


def get_config():
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "password"),
        "source_db": os.environ.get("NEO4J_DATABASE", "jsongraph"),
        "target_db": "hybridgraph",
        "task_db": os.environ.get("TASK_DB", "./tasks.db"),
    }


def setup_apoc_trigger(driver, config: dict, interval_seconds: int = 60):
    """
    Setup APOC-based sync mechanism.

    Note: APOC triggers work per-database, so we use a different approach:
    1. Add a trigger on jsongraph that marks data as 'pending' sync
    2. Use apoc.periodic.repeat to call sync periodically
    """
    print("\nSetting up APOC-based sync...")

    # First, create a stored procedure wrapper for the sync
    # Since cross-db sync requires external process, we'll use a flag-based approach

    with driver.session(database=config["source_db"]) as session:
        # Create trigger that marks new data for sync
        print("  Creating trigger on jsongraph for new data...")

        try:
            # Remove existing trigger if present
            session.run("CALL apoc.trigger.drop('mark_for_sync', {})")
        except:
            pass

        # Add trigger that sets sync_status = 'pending' on new nodes
        session.run("""
            CALL apoc.trigger.install(
                'jsongraph',
                'mark_for_sync',
                'UNWIND $createdNodes AS n
                 WITH n WHERE n:Data AND n.sync_status IS NULL
                 SET n.sync_status = "pending"',
                {phase: 'afterAsync'}
            )
        """)
        print("    ✓ Trigger 'mark_for_sync' installed")

        # Create index for efficient sync queries
        session.run("""
            CREATE INDEX data_sync_pending IF NOT EXISTS
            FOR (d:Data) ON (d.sync_status)
        """)
        print("    ✓ Sync status index created")

    print(f"""
  APOC trigger configured:
  - New :Data nodes automatically marked as sync_status='pending'
  - Run sync_to_hybrid_task.py to process pending items

  To check trigger status:
    CALL apoc.trigger.list('jsongraph')

  To manually trigger sync:
    python sync_to_hybrid_task.py
""")


def setup_stack_runner_task(config: dict, interval_seconds: int = 60):
    """Register sync task in stack runner database."""
    print("\nSetting up Stack Runner task...")

    conn = sqlite3.connect(config["task_db"])

    # Register the sync task
    conn.execute("""
        INSERT OR REPLACE INTO tasks
        (task_id, task_type, code, parameters_json, working_dir, env_json, timeout_seconds, enabled)
        VALUES (?, ?, ?, ?, NULL, ?, ?, 1)
    """, (
        "sync_to_hybrid",
        "python_file",
        "sync_to_hybrid_task.py",
        json.dumps({"limit": 50}),
        json.dumps({
            "SOURCE_DB": config["source_db"],
            "TARGET_DB": config["target_db"],
        }),
        300,  # 5 minute timeout
    ))

    # Create a periodic sync task that re-queues itself
    conn.execute("""
        INSERT OR REPLACE INTO tasks
        (task_id, task_type, code, parameters_json, working_dir, env_json, timeout_seconds, enabled)
        VALUES (?, ?, ?, ?, NULL, '{}', ?, 1)
    """, (
        "periodic_sync",
        "python",
        f'''
import os
import json
import time

params = json.loads(os.environ.get('TASK_PARAMS', '{{}}'))
context = json.loads(os.environ.get('TASK_CONTEXT', '{{}}'))

interval = params.get('interval_seconds', {interval_seconds})

# Import and run sync
import subprocess
result = subprocess.run(
    ['python', 'sync_to_hybrid_task.py', '--limit', '50'],
    capture_output=True, text=True, timeout=300
)

print(result.stdout)
if result.stderr:
    print(result.stderr, file=__import__('sys').stderr)

# Schedule next run
task_result = {{
    "__task_result__": True,
    "output": {{"sync_output": result.stdout[:1000], "exit_code": result.returncode}},
    "variables": {{"last_periodic_sync": __import__('datetime').datetime.now().isoformat()}},
    "decisions": [f"Sync completed with exit code {{result.returncode}}"],
    "push_tasks": [
        {{
            "task_id": "periodic_sync",
            "parameters": {{"interval_seconds": interval, "run_number": params.get('run_number', 0) + 1}},
            "reason": f"Schedule next sync in {{interval}}s"
        }}
    ] if params.get('continuous', False) else []
}}

print(json.dumps(task_result))
'''.strip(),
        json.dumps({"interval_seconds": interval_seconds, "continuous": False}),
        600,  # 10 minute timeout
    ))

    conn.commit()
    conn.close()

    print(f"""    ✓ Task 'sync_to_hybrid' registered
    ✓ Task 'periodic_sync' registered

  To run manually:
    python stack_runner.py -v start sync_to_hybrid

  To run continuously (self-rescheduling):
    python stack_runner.py -v start periodic_sync --params '{{"continuous": true, "interval_seconds": {interval_seconds}}}'
""")


def create_cron_script(config: dict, interval_seconds: int = 60):
    """Create a shell script for cron-based execution."""
    script_content = f'''#!/bin/bash
# Auto-sync jsongraph to hybridgraph
# Add to crontab: * * * * * /path/to/sync_cron.sh

cd "$(dirname "$0")"

export NEO4J_URI="{config['uri']}"
export NEO4J_USER="{config['user']}"
export NEO4J_PASSWORD="{config['password']}"
export SOURCE_DB="{config['source_db']}"
export TARGET_DB="{config['target_db']}"

# Run sync (with lock to prevent overlapping runs)
LOCKFILE="/tmp/jsongraph_sync.lock"

if [ -f "$LOCKFILE" ]; then
    # Check if lock is stale (older than 10 minutes)
    if [ $(find "$LOCKFILE" -mmin +10 2>/dev/null) ]; then
        rm -f "$LOCKFILE"
    else
        echo "Sync already running, skipping..."
        exit 0
    fi
fi

touch "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

python3 sync_to_hybrid_task.py --limit 100 --quiet >> /tmp/jsongraph_sync.log 2>&1
'''

    script_path = os.path.join(os.path.dirname(__file__), "sync_cron.sh")
    with open(script_path, "w") as f:
        f.write(script_content)
    os.chmod(script_path, 0o755)

    print(f"""
  Cron script created: {script_path}

  To add to crontab (runs every minute):
    crontab -e
    # Add line: * * * * * {os.path.abspath(script_path)}
""")


def show_sync_status(driver, config: dict):
    """Show current sync status."""
    print("\n" + "=" * 60)
    print("SYNC STATUS")
    print("=" * 60)

    with driver.session(database=config["source_db"]) as session:
        # Count by sync status
        result = session.run("""
            MATCH (d:Data)
            RETURN d.sync_status AS status, count(*) AS cnt
        """)
        print("\njsongraph sync status:")
        for r in result:
            status = r["status"] or "not_set"
            print(f"  {status}: {r['cnt']:,} nodes")

    with driver.session(database=config["target_db"]) as session:
        result = session.run("""
            MATCH (s:Source)
            RETURN count(s) AS sources,
                   sum(s.node_count) AS original_nodes
        """)
        r = result.single()
        print(f"\nhybridgraph status:")
        print(f"  Sources: {r['sources']}")

        result = session.run("MATCH (s:Structure) RETURN count(s) AS cnt")
        print(f"  Structures: {result.single()['cnt']:,}")

        result = session.run("MATCH (c:Content) RETURN count(c) AS cnt")
        print(f"  Content: {result.single()['cnt']:,}")


def main():
    parser = argparse.ArgumentParser(description="Setup automatic sync")
    parser.add_argument("--method", choices=["apoc", "task", "cron", "all", "status"],
                        default="all", help="Sync method to setup")
    parser.add_argument("--interval", type=int, default=60,
                        help="Sync interval in seconds (for periodic methods)")
    args = parser.parse_args()

    config = get_config()
    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    print("=" * 60)
    print("AUTO-SYNC SETUP")
    print("=" * 60)
    print(f"Source: {config['source_db']}")
    print(f"Target: {config['target_db']}")

    try:
        if args.method == "status":
            show_sync_status(driver, config)
        elif args.method in ["apoc", "all"]:
            setup_apoc_trigger(driver, config, args.interval)

        if args.method in ["task", "all"]:
            setup_stack_runner_task(config, args.interval)

        if args.method in ["cron", "all"]:
            create_cron_script(config, args.interval)

        if args.method != "status":
            show_sync_status(driver, config)

            print("\n" + "=" * 60)
            print("NEXT STEPS")
            print("=" * 60)
            print("""
  1. Test manual sync:
     python sync_to_hybrid_task.py --limit 10

  2. For continuous sync, choose one method:

     a) Stack Runner (recommended for your setup):
        python stack_runner.py -v start periodic_sync --params '{"continuous": true}'

     b) Cron (simple, reliable):
        crontab -e
        # Add: * * * * * /path/to/sync_cron.sh

     c) Manual on-demand:
        python sync_to_hybrid_task.py
""")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
