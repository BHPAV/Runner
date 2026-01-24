#!/usr/bin/env python3
"""
APOC Trigger Setup

Installs Neo4j APOC triggers for the unified agent-runner system:

1. resolve_dependencies - Unblock requests when dependencies complete
2. cascade_on_source - Trigger new requests based on cascade rules
3. mark_sync_pending - Mark new data nodes for sync

Prerequisites:
- Neo4j APOC plugin installed
- apoc.trigger.enabled=true in neo4j.conf

Usage:
    python -m runner.triggers.setup --install
    python -m runner.triggers.setup --status
    python -m runner.triggers.setup --remove
"""

import argparse
import json
import os
import sys
from typing import Dict, List

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from runner.utils.neo4j import get_driver, get_config


# Trigger definitions
TRIGGERS = {
    "resolve_dependencies": {
        "description": "Unblock requests when their dependencies complete",
        "statement": """
            UNWIND $committedNodes AS n
            WITH n WHERE n:TaskRequest AND n.status = 'done'
            MATCH (waiting:TaskRequest)-[:DEPENDS_ON]->(n)
            WHERE waiting.status = 'blocked'
            AND NOT EXISTS {
                MATCH (waiting)-[:DEPENDS_ON]->(other:TaskRequest)
                WHERE other.status <> 'done'
            }
            SET waiting.status = 'pending'
            RETURN count(waiting) as unblocked
        """,
        "selector": {"phase": "afterAsync"},
    },
    "cascade_on_source": {
        "description": "Create new TaskRequests based on CascadeRules when Sources are created",
        "statement": """
            UNWIND $createdNodes AS n
            WITH n WHERE n:Source
            MATCH (rule:CascadeRule {enabled: true})
            WHERE rule.source_kind IS NULL OR n.kind = rule.source_kind
            CREATE (req:TaskRequest {
                request_id: randomUUID(),
                task_id: rule.task_id,
                parameters: apoc.text.replace(
                    coalesce(rule.parameter_template, '{}'),
                    '\\\\$source\\\\.source_id',
                    coalesce(n.source_id, '')
                ),
                status: 'pending',
                priority: coalesce(rule.priority, 50),
                requester: 'trigger:' + rule.rule_id,
                created_at: datetime()
            })
            CREATE (req)-[:TRIGGERED_BY]->(rule)
            RETURN count(req) as created
        """,
        "selector": {"phase": "afterAsync"},
    },
    "mark_sync_pending": {
        "description": "Mark new Data nodes for sync to hybridgraph",
        "statement": """
            UNWIND $createdNodes AS n
            WITH n WHERE n:Data AND n.sync_status IS NULL
            SET n.sync_status = 'pending'
            RETURN count(n) as marked
        """,
        "selector": {"phase": "after"},
    },
}


def check_apoc_available(session) -> bool:
    """Check if APOC triggers are available."""
    try:
        result = session.run("CALL apoc.trigger.list() YIELD name RETURN count(*) as count")
        result.single()
        return True
    except Exception as e:
        if "apoc" in str(e).lower():
            return False
        raise


def install_triggers(database: str = None, verbose: bool = False) -> Dict[str, str]:
    """
    Install all APOC triggers.

    Args:
        database: Target database (default: hybridgraph)
        verbose: Print progress

    Returns:
        Dict mapping trigger names to installation status
    """
    config = get_config()
    database = database or config.get("target_db", "hybridgraph")

    driver = get_driver()
    results = {}

    try:
        with driver.session(database=database) as session:
            # Check APOC availability
            if not check_apoc_available(session):
                return {"error": "APOC triggers not available. Ensure APOC is installed and apoc.trigger.enabled=true"}

            for name, trigger_def in TRIGGERS.items():
                if verbose:
                    print(f"Installing trigger: {name}")
                    print(f"  {trigger_def['description']}")

                try:
                    # Remove existing trigger first (for idempotency)
                    session.run(f"CALL apoc.trigger.remove('{name}')")
                except Exception:
                    pass  # Trigger may not exist

                try:
                    # Install trigger
                    session.run(
                        """
                        CALL apoc.trigger.add(
                            $name,
                            $statement,
                            $selector
                        )
                        """,
                        name=name,
                        statement=trigger_def["statement"].strip(),
                        selector=trigger_def["selector"]
                    )
                    results[name] = "installed"

                    if verbose:
                        print(f"  ✓ Installed")

                except Exception as e:
                    results[name] = f"error: {e}"
                    if verbose:
                        print(f"  ✗ Error: {e}")

    finally:
        driver.close()

    return results


def remove_triggers(database: str = None, verbose: bool = False) -> Dict[str, str]:
    """
    Remove all APOC triggers.

    Args:
        database: Target database (default: hybridgraph)
        verbose: Print progress

    Returns:
        Dict mapping trigger names to removal status
    """
    config = get_config()
    database = database or config.get("target_db", "hybridgraph")

    driver = get_driver()
    results = {}

    try:
        with driver.session(database=database) as session:
            for name in TRIGGERS.keys():
                if verbose:
                    print(f"Removing trigger: {name}")

                try:
                    session.run(f"CALL apoc.trigger.remove('{name}')")
                    results[name] = "removed"

                    if verbose:
                        print(f"  ✓ Removed")

                except Exception as e:
                    if "not found" in str(e).lower():
                        results[name] = "not found"
                        if verbose:
                            print(f"  - Not found")
                    else:
                        results[name] = f"error: {e}"
                        if verbose:
                            print(f"  ✗ Error: {e}")

    finally:
        driver.close()

    return results


def get_trigger_status(database: str = None) -> Dict[str, any]:
    """
    Get status of all triggers.

    Args:
        database: Target database (default: hybridgraph)

    Returns:
        Dict with trigger status information
    """
    config = get_config()
    database = database or config.get("target_db", "hybridgraph")

    driver = get_driver()

    try:
        with driver.session(database=database) as session:
            # Check APOC availability
            if not check_apoc_available(session):
                return {"error": "APOC triggers not available"}

            # List installed triggers
            result = session.run("CALL apoc.trigger.list() YIELD name, statement, paused RETURN *")

            installed = {}
            for record in result:
                installed[record["name"]] = {
                    "paused": record["paused"],
                    "statement_preview": record["statement"][:100] + "..." if len(record["statement"]) > 100 else record["statement"]
                }

            # Build status report
            status = {
                "database": database,
                "apoc_available": True,
                "triggers": {}
            }

            for name, trigger_def in TRIGGERS.items():
                if name in installed:
                    status["triggers"][name] = {
                        "status": "paused" if installed[name]["paused"] else "active",
                        "description": trigger_def["description"],
                    }
                else:
                    status["triggers"][name] = {
                        "status": "not installed",
                        "description": trigger_def["description"],
                    }

            # Check for unknown triggers
            for name in installed:
                if name not in TRIGGERS:
                    status["triggers"][name] = {
                        "status": "active (unknown)",
                        "description": "Not part of runner triggers",
                    }

            return status

    finally:
        driver.close()


def pause_trigger(name: str, database: str = None) -> bool:
    """Pause a trigger."""
    config = get_config()
    database = database or config.get("target_db", "hybridgraph")

    driver = get_driver()
    try:
        with driver.session(database=database) as session:
            session.run(f"CALL apoc.trigger.pause('{name}')")
            return True
    except Exception:
        return False
    finally:
        driver.close()


def resume_trigger(name: str, database: str = None) -> bool:
    """Resume a paused trigger."""
    config = get_config()
    database = database or config.get("target_db", "hybridgraph")

    driver = get_driver()
    try:
        with driver.session(database=database) as session:
            session.run(f"CALL apoc.trigger.resume('{name}')")
            return True
    except Exception:
        return False
    finally:
        driver.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage APOC triggers for the runner system"
    )
    parser.add_argument(
        "--database", "-d",
        help="Target database (default: hybridgraph)"
    )
    parser.add_argument(
        "--install", "-i",
        action="store_true",
        help="Install all triggers"
    )
    parser.add_argument(
        "--remove", "-r",
        action="store_true",
        help="Remove all triggers"
    )
    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Show trigger status"
    )
    parser.add_argument(
        "--pause",
        metavar="NAME",
        help="Pause a specific trigger"
    )
    parser.add_argument(
        "--resume",
        metavar="NAME",
        help="Resume a paused trigger"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    args = parser.parse_args()

    # Default to status if no action specified
    if not any([args.install, args.remove, args.status, args.pause, args.resume]):
        args.status = True

    if args.install:
        print("Installing triggers...")
        results = install_triggers(args.database, args.verbose)
        if not args.verbose:
            print(json.dumps(results, indent=2))

    elif args.remove:
        print("Removing triggers...")
        results = remove_triggers(args.database, args.verbose)
        if not args.verbose:
            print(json.dumps(results, indent=2))

    elif args.pause:
        if pause_trigger(args.pause, args.database):
            print(f"Trigger '{args.pause}' paused")
        else:
            print(f"Failed to pause trigger '{args.pause}'")

    elif args.resume:
        if resume_trigger(args.resume, args.database):
            print(f"Trigger '{args.resume}' resumed")
        else:
            print(f"Failed to resume trigger '{args.resume}'")

    elif args.status:
        status = get_trigger_status(args.database)
        print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
