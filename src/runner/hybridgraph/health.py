#!/usr/bin/env python3
"""
Health monitoring for hybridgraph.

Checks for:
- Orphaned nodes (unreachable from any Source)
- Incorrect ref_counts (don't match actual references)
- Deduplication statistics
- Integrity issues (missing relationships)

Usage:
  python hybridgraph_health_task.py [--fix] [--verbose]
  Via stack runner: as 'hybridgraph_health' task
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List

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
        "database": os.environ.get("TARGET_DB", "hybridgraph"),
    }


class HealthChecker:
    """Health checker for hybridgraph database."""

    def __init__(self, driver, database: str):
        self.driver = driver
        self.database = database
        self.issues = []
        self.warnings = []
        self.stats = {}

    def add_issue(self, category: str, message: str, count: int = 0, details: List = None):
        self.issues.append({
            "category": category,
            "message": message,
            "count": count,
            "details": details or [],
        })

    def add_warning(self, category: str, message: str, count: int = 0):
        self.warnings.append({
            "category": category,
            "message": message,
            "count": count,
        })

    def check_orphaned_structures(self) -> int:
        """Check for Structure nodes with no incoming relationships."""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (s:Structure)
                WHERE NOT ()-[:HAS_ROOT]->(s) AND NOT ()-[:CONTAINS]->(s)
                RETURN s.merkle AS merkle, s.kind AS kind, s.key AS key
                LIMIT 20
            """)

            orphans = [dict(r) for r in result]

            result = session.run("""
                MATCH (s:Structure)
                WHERE NOT ()-[:HAS_ROOT]->(s) AND NOT ()-[:CONTAINS]->(s)
                RETURN count(s) AS count
            """)
            total = result.single()["count"]

            if total > 0:
                self.add_issue(
                    "orphaned_nodes",
                    f"{total} Structure nodes have no incoming relationships",
                    count=total,
                    details=orphans[:10]
                )

            self.stats["orphaned_structures"] = total
            return total

    def check_orphaned_content(self) -> int:
        """Check for Content nodes with no incoming relationships."""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (c:Content)
                WHERE NOT ()-[:HAS_VALUE]->(c)
                RETURN c.hash AS hash, c.kind AS kind, c.key AS key
                LIMIT 20
            """)

            orphans = [dict(r) for r in result]

            result = session.run("""
                MATCH (c:Content)
                WHERE NOT ()-[:HAS_VALUE]->(c)
                RETURN count(c) AS count
            """)
            total = result.single()["count"]

            if total > 0:
                self.add_issue(
                    "orphaned_nodes",
                    f"{total} Content nodes have no incoming relationships",
                    count=total,
                    details=orphans[:10]
                )

            self.stats["orphaned_content"] = total
            return total

    def check_sources_without_roots(self) -> int:
        """Check for Source nodes without HAS_ROOT relationships."""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (src:Source)
                WHERE NOT (src)-[:HAS_ROOT]->()
                RETURN src.source_id AS source_id
            """)

            sources = [r["source_id"] for r in result]

            if sources:
                self.add_issue(
                    "missing_relationships",
                    f"{len(sources)} Sources have no HAS_ROOT relationship",
                    count=len(sources),
                    details=sources[:10]
                )

            self.stats["sources_without_root"] = len(sources)
            return len(sources)

    def check_ref_count_accuracy(self) -> Dict:
        """Check if ref_counts match actual references (comprehensive)."""
        with self.driver.session(database=self.database) as session:
            # Check Structure ref_counts - count sources whose tree includes each structure
            result = session.run("""
                MATCH (s:Structure)
                OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..100]->(s)
                WITH s, count(DISTINCT src) AS tree_refs

                // Also count direct HAS_ROOT references
                OPTIONAL MATCH (src2:Source)-[:HAS_ROOT]->(s)
                WITH s, tree_refs + count(DISTINCT src2) AS actual_refs

                WHERE s.ref_count IS NULL OR s.ref_count <> actual_refs
                RETURN s.merkle AS merkle, s.ref_count AS stored, actual_refs AS actual
                LIMIT 20
            """)

            structure_mismatches = [dict(r) for r in result]

            # Count total structure mismatches
            result = session.run("""
                MATCH (s:Structure)
                OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..100]->(s)
                WITH s, count(DISTINCT src) AS tree_refs
                OPTIONAL MATCH (src2:Source)-[:HAS_ROOT]->(s)
                WITH s, tree_refs + count(DISTINCT src2) AS actual_refs
                WHERE s.ref_count IS NULL OR s.ref_count <> actual_refs
                RETURN count(*) AS count
            """)
            structure_mismatch_count = result.single()["count"]

            if structure_mismatch_count > 0:
                self.add_warning(
                    "ref_count_mismatch",
                    f"{structure_mismatch_count} Structure nodes have incorrect ref_count (comprehensive check)",
                    count=structure_mismatch_count
                )

            # Check Content ref_counts
            result = session.run("""
                MATCH (c:Content)
                OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..100]->(:Structure)-[:HAS_VALUE]->(c)
                WITH c, count(DISTINCT src) AS tree_refs

                // Also count direct references from root structures
                OPTIONAL MATCH (src2:Source)-[:HAS_ROOT]->(:Structure)-[:HAS_VALUE]->(c)
                WITH c, tree_refs + count(DISTINCT src2) AS actual_refs

                WHERE c.ref_count IS NULL OR c.ref_count <> actual_refs
                RETURN c.hash AS hash, c.ref_count AS stored, actual_refs AS actual
                LIMIT 20
            """)

            content_mismatches = [dict(r) for r in result]

            # Count total content mismatches
            result = session.run("""
                MATCH (c:Content)
                OPTIONAL MATCH (src:Source)-[:HAS_ROOT]->(:Structure)-[:CONTAINS*0..100]->(:Structure)-[:HAS_VALUE]->(c)
                WITH c, count(DISTINCT src) AS tree_refs
                OPTIONAL MATCH (src2:Source)-[:HAS_ROOT]->(:Structure)-[:HAS_VALUE]->(c)
                WITH c, tree_refs + count(DISTINCT src2) AS actual_refs
                WHERE c.ref_count IS NULL OR c.ref_count <> actual_refs
                RETURN count(*) AS count
            """)
            content_mismatch_count = result.single()["count"]

            if content_mismatch_count > 0:
                self.add_warning(
                    "content_ref_count_mismatch",
                    f"{content_mismatch_count} Content nodes have incorrect ref_count",
                    count=content_mismatch_count
                )

            # Check for null or negative ref_counts
            result = session.run("""
                MATCH (n)
                WHERE (n:Structure OR n:Content)
                  AND (n.ref_count IS NULL OR n.ref_count < 0)
                RETURN labels(n)[0] AS label, count(*) AS count
            """)

            for r in result:
                if r["count"] > 0:
                    self.add_warning(
                        "invalid_ref_count",
                        f"{r['count']} {r['label']} nodes have null or negative ref_count",
                        count=r["count"]
                    )

            self.stats["structure_ref_count_mismatches"] = structure_mismatch_count
            self.stats["content_ref_count_mismatches"] = content_mismatch_count
            return {
                "structure_mismatches": structure_mismatch_count,
                "structure_details": structure_mismatches[:10],
                "content_mismatches": content_mismatch_count,
                "content_details": content_mismatches[:10],
            }

    def check_duplicate_hashes(self) -> int:
        """Check for duplicate hashes (should not happen with constraints)."""
        with self.driver.session(database=self.database) as session:
            # This should return 0 if constraints are working
            result = session.run("""
                MATCH (c:Content)
                WITH c.hash AS hash, collect(c) AS nodes
                WHERE size(nodes) > 1
                RETURN hash, size(nodes) AS count
            """)

            duplicates = [dict(r) for r in result]

            if duplicates:
                self.add_issue(
                    "duplicate_hashes",
                    f"{len(duplicates)} duplicate Content hashes found",
                    count=len(duplicates),
                    details=duplicates[:10]
                )

            self.stats["duplicate_hashes"] = len(duplicates)
            return len(duplicates)

    def get_overall_stats(self) -> Dict:
        """Get overall database statistics."""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (src:Source)
                WITH count(src) AS sources

                OPTIONAL MATCH (s:Structure)
                WITH sources, count(s) AS structures

                OPTIONAL MATCH (c:Content)
                WITH sources, structures, count(c) AS contents

                OPTIONAL MATCH ()-[r:HAS_ROOT]->()
                WITH sources, structures, contents, count(r) AS has_root

                OPTIONAL MATCH ()-[r:CONTAINS]->()
                WITH sources, structures, contents, has_root, count(r) AS contains

                OPTIONAL MATCH ()-[r:HAS_VALUE]->()
                RETURN sources, structures, contents, has_root, contains, count(r) AS has_value
            """)

            record = result.single()
            return {
                "sources": record["sources"],
                "structures": record["structures"],
                "contents": record["contents"],
                "relationships": {
                    "HAS_ROOT": record["has_root"],
                    "CONTAINS": record["contains"],
                    "HAS_VALUE": record["has_value"],
                },
            }

    def get_deduplication_stats(self) -> Dict:
        """Get deduplication effectiveness statistics."""
        with self.driver.session(database=self.database) as session:
            # Content deduplication
            result = session.run("""
                MATCH (c:Content)
                WITH sum(c.ref_count) AS total_refs,
                     count(c) AS unique_count,
                     max(c.ref_count) AS max_refs
                RETURN total_refs, unique_count, max_refs
            """)
            content = result.single()

            # Structure deduplication
            result = session.run("""
                MATCH (s:Structure)
                WITH sum(s.ref_count) AS total_refs,
                     count(s) AS unique_count,
                     max(s.ref_count) AS max_refs
                RETURN total_refs, unique_count, max_refs
            """)
            structure = result.single()

            content_total = content["total_refs"] or 0
            content_unique = content["unique_count"] or 0
            structure_total = structure["total_refs"] or 0
            structure_unique = structure["unique_count"] or 0

            return {
                "content": {
                    "unique": content_unique,
                    "total_refs": content_total,
                    "max_refs": content["max_refs"] or 0,
                    "dedup_ratio": f"{(content_total - content_unique) / content_total * 100:.1f}%" if content_total > 0 else "N/A",
                },
                "structure": {
                    "unique": structure_unique,
                    "total_refs": structure_total,
                    "max_refs": structure["max_refs"] or 0,
                    "dedup_ratio": f"{(structure_total - structure_unique) / structure_total * 100:.1f}%" if structure_total > 0 else "N/A",
                },
            }

    def run_all_checks(self) -> Dict:
        """Run all health checks."""
        self.stats["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Run checks
        self.check_orphaned_structures()
        self.check_orphaned_content()
        self.check_sources_without_roots()
        self.check_ref_count_accuracy()
        self.check_duplicate_hashes()

        # Get stats
        self.stats["overall"] = self.get_overall_stats()
        self.stats["deduplication"] = self.get_deduplication_stats()

        # Determine health status
        if self.issues:
            status = "unhealthy"
        elif self.warnings:
            status = "degraded"
        else:
            status = "healthy"

        return {
            "status": status,
            "timestamp": self.stats["timestamp"],
            "stats": self.stats,
            "issues": self.issues,
            "warnings": self.warnings,
        }


def fix_issues(driver, database: str, verbose: bool = True) -> Dict:
    """Attempt to fix detected issues."""
    fixes = {
        "orphaned_structures_deleted": 0,
        "orphaned_content_deleted": 0,
        "ref_counts_fixed": 0,
    }

    with driver.session(database=database) as session:
        # Delete orphaned structures
        result = session.run("""
            MATCH (s:Structure)
            WHERE NOT ()-[:HAS_ROOT]->(s) AND NOT ()-[:CONTAINS]->(s)
            DETACH DELETE s
            RETURN count(*) AS deleted
        """)
        fixes["orphaned_structures_deleted"] = result.single()["deleted"]

        # Delete orphaned content
        result = session.run("""
            MATCH (c:Content)
            WHERE NOT ()-[:HAS_VALUE]->(c)
            DELETE c
            RETURN count(*) AS deleted
        """)
        fixes["orphaned_content_deleted"] = result.single()["deleted"]

        # Fix null/negative ref_counts
        result = session.run("""
            MATCH (n)
            WHERE (n:Structure OR n:Content)
              AND (n.ref_count IS NULL OR n.ref_count < 0)
            SET n.ref_count = 0
            RETURN count(*) AS fixed
        """)
        fixes["ref_counts_fixed"] = result.single()["fixed"]

        if verbose:
            print(f"Fixed issues:")
            print(f"  Orphaned structures deleted: {fixes['orphaned_structures_deleted']}")
            print(f"  Orphaned content deleted: {fixes['orphaned_content_deleted']}")
            print(f"  Ref counts fixed: {fixes['ref_counts_fixed']}")

    return fixes


def run_health_check(fix: bool = False, verbose: bool = True) -> Dict:
    """Run health check with optional fixing."""
    config = get_config()
    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))

    try:
        checker = HealthChecker(driver, config["database"])
        report = checker.run_all_checks()

        if verbose:
            print(f"\nHealth Status: {report['status'].upper()}")
            print(f"Timestamp: {report['timestamp']}")

            print(f"\nOverall Statistics:")
            stats = report["stats"]["overall"]
            print(f"  Sources: {stats['sources']}")
            print(f"  Structures: {stats['structures']}")
            print(f"  Contents: {stats['contents']}")
            print(f"  Relationships: {sum(stats['relationships'].values())}")

            print(f"\nDeduplication:")
            dedup = report["stats"]["deduplication"]
            print(f"  Content: {dedup['content']['unique']} unique, {dedup['content']['dedup_ratio']} deduplicated")
            print(f"  Structure: {dedup['structure']['unique']} unique, {dedup['structure']['dedup_ratio']} deduplicated")

            if report["issues"]:
                print(f"\nIssues ({len(report['issues'])}):")
                for issue in report["issues"]:
                    print(f"  [{issue['category']}] {issue['message']}")

            if report["warnings"]:
                print(f"\nWarnings ({len(report['warnings'])}):")
                for warning in report["warnings"]:
                    print(f"  [{warning['category']}] {warning['message']}")

        if fix and (report["issues"] or report["warnings"]):
            print("\nAttempting fixes...")
            fixes = fix_issues(driver, config["database"], verbose)
            report["fixes"] = fixes

    finally:
        driver.close()

    return report


def main():
    parser = argparse.ArgumentParser(description="Health check for hybridgraph")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Check for task params (stack runner mode)
    if os.environ.get("TASK_PARAMS"):
        params = json.loads(os.environ.get("TASK_PARAMS", "{}"))
        fix = params.get("fix", False)
        as_json = params.get("json", False)
    else:
        fix = args.fix
        as_json = args.json

    if not args.quiet and not as_json:
        print("=" * 60)
        print("HYBRIDGRAPH HEALTH CHECK")
        print("=" * 60)

    report = run_health_check(fix=fix, verbose=not args.quiet and not as_json)

    if as_json:
        print(json.dumps(report, indent=2, default=str))

    # Output for stack runner
    if os.environ.get("TASK_PARAMS"):
        task_result = {
            "__task_result__": True,
            "output": report,
            "variables": {
                "health_status": report["status"],
                "issue_count": len(report["issues"]),
                "warning_count": len(report["warnings"]),
            },
            "decisions": [
                f"Health status: {report['status']}",
                f"Issues: {len(report['issues'])}, Warnings: {len(report['warnings'])}",
            ],
        }
        print(json.dumps(task_result))

    # Exit with appropriate code
    if report["status"] == "unhealthy":
        sys.exit(2)
    elif report["status"] == "degraded":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
