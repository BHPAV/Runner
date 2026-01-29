#!/usr/bin/env python3
"""
Migration: Add TaskRequest schema to Neo4j hybridgraph database.

This creates the schema for agent-submitted task requests:
- :TaskRequest nodes with status lifecycle
- :CascadeRule nodes for automatic task triggering
- Constraints and indexes for efficient querying

This migration is idempotent - running multiple times is safe.
"""

import os
import sys
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from runner.utils.neo4j import get_driver, get_config


# Schema version for tracking
SCHEMA_VERSION = "1.0.0"


CONSTRAINTS = [
    # Unique constraint on request_id
    """
    CREATE CONSTRAINT task_request_id IF NOT EXISTS
    FOR (r:TaskRequest) REQUIRE r.request_id IS UNIQUE
    """,
    # Unique constraint on cascade rule ID
    """
    CREATE CONSTRAINT cascade_rule_id IF NOT EXISTS
    FOR (r:CascadeRule) REQUIRE r.rule_id IS UNIQUE
    """,
]


INDEXES = [
    # Primary lookup: pending requests by priority
    """
    CREATE INDEX task_request_status_priority IF NOT EXISTS
    FOR (r:TaskRequest) ON (r.status, r.priority)
    """,
    # Lookup by requester
    """
    CREATE INDEX task_request_requester IF NOT EXISTS
    FOR (r:TaskRequest) ON (r.requester)
    """,
    # Lookup by task_id
    """
    CREATE INDEX task_request_task_id IF NOT EXISTS
    FOR (r:TaskRequest) ON (r.task_id)
    """,
    # Cascade rules by enabled status
    """
    CREATE INDEX cascade_rule_enabled IF NOT EXISTS
    FOR (r:CascadeRule) ON (r.enabled)
    """,
]


def check_schema_exists(session) -> bool:
    """Check if the TaskRequest schema already exists."""
    result = session.run("""
        SHOW CONSTRAINTS
        YIELD name
        WHERE name = 'task_request_id'
        RETURN count(*) > 0 as exists
    """)
    record = result.single()
    return record["exists"] if record else False


def create_constraints(session):
    """Create unique constraints."""
    for constraint in CONSTRAINTS:
        try:
            session.run(constraint.strip())
            print(f"  Created constraint: {constraint.split('IF NOT EXISTS')[0].strip()}")
        except Exception as e:
            if "already exists" in str(e).lower():
                print(f"  Constraint already exists (skipped)")
            else:
                raise


def create_indexes(session):
    """Create indexes for efficient querying."""
    for index in INDEXES:
        try:
            session.run(index.strip())
            print(f"  Created index: {index.split('IF NOT EXISTS')[0].strip()}")
        except Exception as e:
            if "already exists" in str(e).lower():
                print(f"  Index already exists (skipped)")
            else:
                raise


def create_schema_version_node(session):
    """Create or update schema version tracking node."""
    session.run("""
        MERGE (s:SchemaVersion {schema_name: 'task_requests'})
        SET s.version = $version,
            s.migrated_at = datetime(),
            s.description = 'TaskRequest and CascadeRule schema for agent-driven task submission'
    """, version=SCHEMA_VERSION)
    print(f"  Schema version set to {SCHEMA_VERSION}")


def create_example_cascade_rule(session):
    """Create an example cascade rule (disabled by default)."""
    result = session.run("""
        MERGE (r:CascadeRule {rule_id: 'example_on_new_source'})
        ON CREATE SET
            r.description = 'Example: Trigger validation when new Source is created',
            r.source_kind = 'json',
            r.task_id = 'validate_json',
            r.parameter_template = '{"source_id": "$source.source_id"}',
            r.priority = 50,
            r.enabled = false,
            r.created_at = datetime()
        RETURN r.rule_id as rule_id,
               CASE WHEN r.created_at = datetime() THEN 'created' ELSE 'exists' END as status
    """)
    record = result.single()
    if record:
        print(f"  Example cascade rule '{record['rule_id']}': {record['status']}")


def migrate(database: str = None):
    """
    Run the migration to add TaskRequest schema.

    Args:
        database: Target database name (defaults to TARGET_DB env var or 'hybridgraph')
    """
    config = get_config()
    database = database or config.get("target_db", "hybridgraph")

    print(f"=" * 60)
    print(f"TaskRequest Schema Migration")
    print(f"=" * 60)
    print(f"Database: {database}")
    print(f"URI: {config['uri']}")
    print(f"Time: {datetime.now().isoformat()}")
    print()

    driver = get_driver()

    try:
        with driver.session(database=database) as session:
            # Check if already migrated
            if check_schema_exists(session):
                print("Schema already exists - checking for updates...")
            else:
                print("Creating new schema...")

            print()
            print("Creating constraints...")
            create_constraints(session)

            print()
            print("Creating indexes...")
            create_indexes(session)

            print()
            print("Setting schema version...")
            create_schema_version_node(session)

            print()
            print("Creating example cascade rule...")
            create_example_cascade_rule(session)

            print()
            print("=" * 60)
            print("Migration complete!")
            print("=" * 60)

            # Print summary
            print()
            print("Summary:")
            result = session.run("""
                MATCH (r:TaskRequest) RETURN count(r) as requests
            """)
            requests = result.single()["requests"]

            result = session.run("""
                MATCH (r:CascadeRule) RETURN count(r) as rules
            """)
            rules = result.single()["rules"]

            print(f"  TaskRequest nodes: {requests}")
            print(f"  CascadeRule nodes: {rules}")

    finally:
        driver.close()


def show_schema(database: str = None):
    """Display the current TaskRequest schema status."""
    config = get_config()
    database = database or config.get("target_db", "hybridgraph")

    driver = get_driver()

    try:
        with driver.session(database=database) as session:
            print(f"TaskRequest Schema Status ({database})")
            print("=" * 50)

            # Schema version
            result = session.run("""
                MATCH (s:SchemaVersion {schema_name: 'task_requests'})
                RETURN s.version as version, s.migrated_at as migrated_at
            """)
            record = result.single()
            if record:
                print(f"Version: {record['version']}")
                print(f"Migrated: {record['migrated_at']}")
            else:
                print("Schema not installed")
                return

            print()

            # Constraints
            print("Constraints:")
            result = session.run("SHOW CONSTRAINTS")
            for record in result:
                if 'TaskRequest' in str(record) or 'CascadeRule' in str(record):
                    print(f"  - {record['name']}")

            print()

            # Indexes
            print("Indexes:")
            result = session.run("SHOW INDEXES")
            for record in result:
                if 'task_request' in str(record['name']).lower() or 'cascade_rule' in str(record['name']).lower():
                    print(f"  - {record['name']}")

            print()

            # Statistics
            print("Statistics:")
            result = session.run("""
                MATCH (r:TaskRequest)
                RETURN r.status as status, count(r) as count
                ORDER BY count DESC
            """)
            for record in result:
                print(f"  TaskRequest ({record['status']}): {record['count']}")

            result = session.run("""
                MATCH (r:CascadeRule)
                RETURN r.enabled as enabled, count(r) as count
            """)
            for record in result:
                status = "enabled" if record["enabled"] else "disabled"
                print(f"  CascadeRule ({status}): {record['count']}")

    finally:
        driver.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TaskRequest schema migration")
    parser.add_argument("--database", "-d", help="Target database (default: hybridgraph)")
    parser.add_argument("--show", action="store_true", help="Show current schema status")

    args = parser.parse_args()

    if args.show:
        show_schema(args.database)
    else:
        migrate(args.database)
