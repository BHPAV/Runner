#!/usr/bin/env python3
"""
Migrate Identifiers from jsongraph to hybridgraph.

Creates :Identifier nodes and links them to :Content nodes that contain matching values.
This enables cross-document entity resolution (e.g., find all documents mentioning an email).

Source Schema (jsongraph):
  :Identifier {kind, value, vtype, object_count, updated_at, sample_raw}
  :JsonNode -[:HAS_IDENTIFIER]-> :Identifier

Target Schema (hybridgraph):
  :Identifier {kind, value, ref_count, created_at}
  :Content -[:HAS_IDENTIFIER]-> :Identifier

Usage:
  python migrate_identifiers_to_hybrid.py [--dry-run] [--kind email]
"""

import argparse
import os
import sys
from datetime import datetime, timezone

try:
    from neo4j import GraphDatabase
except ImportError:
    print("Error: neo4j driver not installed. Run: pip install neo4j")
    sys.exit(1)


def get_config():
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "password"),
        "source_db": os.environ.get("NEO4J_DATABASE", "jsongraph"),
        "target_db": "hybridgraph",
    }


def setup_schema(driver, target_db: str):
    """Create constraints and indexes for Identifier nodes."""
    print("Setting up Identifier schema...")

    with driver.session(database=target_db) as session:
        # Create constraint for unique identifiers
        session.run("""
            CREATE CONSTRAINT identifier_unique IF NOT EXISTS
            FOR (i:Identifier) REQUIRE (i.kind, i.value) IS UNIQUE
        """)

        # Create indexes
        session.run("""
            CREATE INDEX identifier_kind IF NOT EXISTS
            FOR (i:Identifier) ON (i.kind)
        """)
        session.run("""
            CREATE INDEX identifier_value IF NOT EXISTS
            FOR (i:Identifier) ON (i.value)
        """)

    print("  Schema setup complete")


def get_identifiers(driver, source_db: str, kind: str = None) -> list:
    """Load all identifiers from jsongraph."""
    print(f"\nLoading identifiers from {source_db}...")

    with driver.session(database=source_db) as session:
        if kind:
            result = session.run("""
                MATCH (i:Identifier {kind: $kind})
                RETURN i.kind AS kind, i.value AS value, i.vtype AS vtype,
                       i.object_count AS object_count, i.sample_raw AS sample_raw
                ORDER BY i.object_count DESC
            """, kind=kind)
        else:
            result = session.run("""
                MATCH (i:Identifier)
                RETURN i.kind AS kind, i.value AS value, i.vtype AS vtype,
                       i.object_count AS object_count, i.sample_raw AS sample_raw
                ORDER BY i.object_count DESC
            """)

        identifiers = [dict(r) for r in result]

    print(f"  Found {len(identifiers):,} identifiers")
    return identifiers


def migrate_identifiers(driver, target_db: str, identifiers: list, dry_run: bool = False):
    """Create Identifier nodes in hybridgraph."""
    print(f"\nMigrating {len(identifiers):,} identifiers...")

    if dry_run:
        print("  DRY RUN - no changes made")
        return

    now = datetime.now(timezone.utc).isoformat()
    batch_size = 500

    with driver.session(database=target_db) as session:
        for i in range(0, len(identifiers), batch_size):
            batch = identifiers[i:i+batch_size]

            # Add timestamp to each identifier
            for ident in batch:
                ident["created_at"] = now

            session.run("""
                UNWIND $batch AS ident
                MERGE (i:Identifier {kind: ident.kind, value: ident.value})
                ON CREATE SET
                    i.vtype = ident.vtype,
                    i.original_object_count = ident.object_count,
                    i.sample_raw = ident.sample_raw,
                    i.created_at = ident.created_at,
                    i.ref_count = 0
            """, batch=batch)

            if (i + batch_size) % 1000 == 0 or i + batch_size >= len(identifiers):
                print(f"    Created {min(i + batch_size, len(identifiers)):,}/{len(identifiers):,} Identifier nodes")


def link_to_content(driver, target_db: str, identifiers: list, dry_run: bool = False):
    """Link Identifier nodes to Content nodes with matching values."""
    print(f"\nLinking identifiers to Content nodes...")

    if dry_run:
        # Just count potential matches
        with driver.session(database=target_db) as session:
            result = session.run("""
                UNWIND $values AS val
                MATCH (c:Content)
                WHERE c.value_str = val OR c.value_str CONTAINS val
                RETURN count(c) AS matches
            """, values=[i["value"] for i in identifiers[:100]])
            count = result.single()["matches"]
            print(f"  DRY RUN - estimated ~{count * len(identifiers) // 100:,} potential links")
        return 0

    total_links = 0
    batch_size = 100

    with driver.session(database=target_db) as session:
        for i in range(0, len(identifiers), batch_size):
            batch = identifiers[i:i+batch_size]
            values = [(ident["kind"], ident["value"]) for ident in batch]

            # Link identifiers to content nodes with exact value match
            result = session.run("""
                UNWIND $values AS pair
                WITH pair[0] AS kind, pair[1] AS value
                MATCH (i:Identifier {kind: kind, value: value})
                MATCH (c:Content)
                WHERE c.value_str = value
                MERGE (c)-[:HAS_IDENTIFIER]->(i)
                WITH i, count(c) AS linked
                SET i.ref_count = linked
                RETURN sum(linked) AS total_linked
            """, values=values)

            record = result.single()
            if record:
                total_links += record["total_linked"]

            if (i + batch_size) % 500 == 0 or i + batch_size >= len(identifiers):
                print(f"    Processed {min(i + batch_size, len(identifiers)):,}/{len(identifiers):,} identifiers, {total_links:,} links created")

    return total_links


def link_partial_matches(driver, target_db: str, dry_run: bool = False):
    """Link identifiers found within larger strings (e.g., email in 'Name <email>')."""
    print(f"\nLinking partial matches (emails in formatted strings)...")

    if dry_run:
        print("  DRY RUN - skipping partial matches")
        return 0

    with driver.session(database=target_db) as session:
        # Find emails within strings like "Name <email@domain.com>"
        result = session.run("""
            MATCH (i:Identifier {kind: 'email'})
            MATCH (c:Content)
            WHERE c.value_str CONTAINS i.value
              AND c.value_str <> i.value
              AND NOT (c)-[:HAS_IDENTIFIER]->(i)
            MERGE (c)-[:HAS_IDENTIFIER]->(i)
            WITH i, count(c) AS new_links
            SET i.ref_count = i.ref_count + new_links
            RETURN sum(new_links) AS total
        """)

        record = result.single()
        total = record["total"] if record else 0
        print(f"    Created {total:,} partial match links")
        return total


def verify_migration(driver, target_db: str):
    """Verify the migration results."""
    print("\nVerifying migration...")

    with driver.session(database=target_db) as session:
        # Count identifiers by kind
        result = session.run("""
            MATCH (i:Identifier)
            RETURN i.kind AS kind, count(i) AS count, sum(i.ref_count) AS total_refs
            ORDER BY count DESC
        """)

        print("\n" + "=" * 60)
        print("IDENTIFIER MIGRATION SUMMARY")
        print("=" * 60)
        print(f"\n{'Kind':<15} {'Count':>10} {'References':>12}")
        print("-" * 40)

        total_idents = 0
        total_refs = 0
        for record in result:
            kind = record["kind"]
            count = record["count"]
            refs = record["total_refs"] or 0
            print(f"{kind:<15} {count:>10,} {refs:>12,}")
            total_idents += count
            total_refs += refs

        print("-" * 40)
        print(f"{'TOTAL':<15} {total_idents:>10,} {total_refs:>12,}")

        # Count relationships
        result = session.run("""
            MATCH ()-[r:HAS_IDENTIFIER]->()
            RETURN count(r) AS rel_count
        """)
        rel_count = result.single()["rel_count"]

        print(f"\nHAS_IDENTIFIER relationships: {rel_count:,}")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Identifiers from jsongraph to hybridgraph"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze without writing to database"
    )
    parser.add_argument(
        "--kind", type=str, default=None,
        help="Only migrate specific identifier kind (e.g., email, hostname)"
    )
    parser.add_argument(
        "--skip-partial", action="store_true",
        help="Skip partial match linking (faster)"
    )
    args = parser.parse_args()

    config = get_config()

    print("=" * 60)
    print("IDENTIFIER MIGRATION TO HYBRIDGRAPH")
    print("=" * 60)
    print(f"Source: {config['source_db']}")
    print(f"Target: {config['target_db']}")
    if args.kind:
        print(f"Kind filter: {args.kind}")
    if args.dry_run:
        print("Mode: DRY RUN")
    print()

    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    try:
        # Step 1: Setup schema
        if not args.dry_run:
            setup_schema(driver, config["target_db"])

        # Step 2: Load identifiers
        identifiers = get_identifiers(driver, config["source_db"], args.kind)

        if not identifiers:
            print("No identifiers found to migrate")
            return

        # Step 3: Create Identifier nodes
        migrate_identifiers(driver, config["target_db"], identifiers, args.dry_run)

        # Step 4: Link to Content nodes
        exact_links = link_to_content(driver, config["target_db"], identifiers, args.dry_run)

        # Step 5: Link partial matches (optional)
        partial_links = 0
        if not args.skip_partial and not args.dry_run:
            partial_links = link_partial_matches(driver, config["target_db"], args.dry_run)

        # Step 6: Verify
        if not args.dry_run:
            verify_migration(driver, config["target_db"])

        print("\nMigration complete!")
        if not args.dry_run:
            print(f"  Total links created: {exact_links + partial_links:,}")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
