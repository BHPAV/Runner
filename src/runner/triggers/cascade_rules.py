#!/usr/bin/env python3
"""
Cascade Rule Management

CascadeRules define automatic task creation when graph events occur.
When a :Source node is created, matching cascade rules create new
:TaskRequest nodes automatically.

Example rule:
    When a Source with kind='json' is created,
    create a TaskRequest for 'validate_json' task
    with parameters {"source_id": "<the new source's id>"}

Usage:
    python -m runner.triggers.cascade_rules list
    python -m runner.triggers.cascade_rules create --rule-id my_rule --task validate_json
    python -m runner.triggers.cascade_rules enable my_rule
    python -m runner.triggers.cascade_rules disable my_rule
    python -m runner.triggers.cascade_rules delete my_rule
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from runner.utils.neo4j import get_driver, get_config


class CascadeRuleManager:
    """
    Manages CascadeRule nodes in Neo4j.

    CascadeRule schema:
        rule_id: str          - Unique identifier
        description: str      - Human-readable description
        source_kind: str      - Match Sources with this kind (null = all)
        task_id: str          - Task to create request for
        parameter_template: str - JSON template with $source.* placeholders
        priority: int         - Priority for created requests
        enabled: bool         - Whether rule is active
        created_at: datetime
    """

    def __init__(self, database: str = None):
        """
        Initialize the manager.

        Args:
            database: Target Neo4j database (default: hybridgraph)
        """
        config = get_config()
        self.database = database or config.get("target_db", "hybridgraph")

    def _get_session(self):
        """Get a Neo4j session."""
        driver = get_driver()
        return driver.session(database=self.database), driver

    def list_rules(self, enabled_only: bool = False) -> List[Dict]:
        """
        List all cascade rules.

        Args:
            enabled_only: Only return enabled rules

        Returns:
            List of rule dictionaries
        """
        session, driver = self._get_session()
        try:
            query = """
                MATCH (r:CascadeRule)
            """
            if enabled_only:
                query += " WHERE r.enabled = true"

            query += """
                OPTIONAL MATCH (req:TaskRequest)-[:TRIGGERED_BY]->(r)
                RETURN r {
                    .rule_id, .description, .source_kind, .task_id,
                    .parameter_template, .priority, .enabled, .created_at
                } as rule,
                count(req) as trigger_count
                ORDER BY r.rule_id
            """

            result = session.run(query)

            rules = []
            for record in result:
                rule = dict(record["rule"])
                rule["trigger_count"] = record["trigger_count"]
                if rule.get("created_at"):
                    rule["created_at"] = str(rule["created_at"])
                rules.append(rule)

            return rules

        finally:
            session.close()
            driver.close()

    def get_rule(self, rule_id: str) -> Optional[Dict]:
        """
        Get a specific cascade rule.

        Args:
            rule_id: The rule identifier

        Returns:
            Rule dictionary or None if not found
        """
        session, driver = self._get_session()
        try:
            result = session.run("""
                MATCH (r:CascadeRule {rule_id: $rule_id})
                OPTIONAL MATCH (req:TaskRequest)-[:TRIGGERED_BY]->(r)
                RETURN r {
                    .rule_id, .description, .source_kind, .task_id,
                    .parameter_template, .priority, .enabled, .created_at
                } as rule,
                count(req) as trigger_count
            """, rule_id=rule_id)

            record = result.single()
            if record and record["rule"]:
                rule = dict(record["rule"])
                rule["trigger_count"] = record["trigger_count"]
                if rule.get("created_at"):
                    rule["created_at"] = str(rule["created_at"])
                return rule

            return None

        finally:
            session.close()
            driver.close()

    def create_rule(
        self,
        rule_id: str,
        task_id: str,
        description: str = None,
        source_kind: str = None,
        parameter_template: str = None,
        priority: int = 50,
        enabled: bool = True,
    ) -> Dict:
        """
        Create a new cascade rule.

        Args:
            rule_id: Unique identifier for the rule
            task_id: Task to create request for
            description: Human-readable description
            source_kind: Match Sources with this kind (None = all)
            parameter_template: JSON template with $source.* placeholders
            priority: Priority for created requests (default: 50)
            enabled: Whether rule is active (default: True)

        Returns:
            Created rule dictionary
        """
        session, driver = self._get_session()
        try:
            # Validate parameter template is valid JSON if provided
            if parameter_template:
                try:
                    json.loads(parameter_template)
                except json.JSONDecodeError as e:
                    return {"error": f"Invalid parameter_template JSON: {e}"}

            result = session.run("""
                MERGE (r:CascadeRule {rule_id: $rule_id})
                ON CREATE SET
                    r.task_id = $task_id,
                    r.description = $description,
                    r.source_kind = $source_kind,
                    r.parameter_template = $parameter_template,
                    r.priority = $priority,
                    r.enabled = $enabled,
                    r.created_at = datetime()
                ON MATCH SET
                    r.task_id = $task_id,
                    r.description = $description,
                    r.source_kind = $source_kind,
                    r.parameter_template = $parameter_template,
                    r.priority = $priority,
                    r.enabled = $enabled
                RETURN r {
                    .rule_id, .task_id, .description, .source_kind,
                    .parameter_template, .priority, .enabled, .created_at
                } as rule
            """,
                rule_id=rule_id,
                task_id=task_id,
                description=description or f"Cascade rule for {task_id}",
                source_kind=source_kind,
                parameter_template=parameter_template or '{"source_id": "$source.source_id"}',
                priority=priority,
                enabled=enabled
            )

            record = result.single()
            if record:
                rule = dict(record["rule"])
                if rule.get("created_at"):
                    rule["created_at"] = str(rule["created_at"])
                return rule

            return {"error": "Failed to create rule"}

        finally:
            session.close()
            driver.close()

    def update_rule(self, rule_id: str, **updates) -> Optional[Dict]:
        """
        Update an existing cascade rule.

        Args:
            rule_id: The rule to update
            **updates: Fields to update (task_id, description, source_kind,
                       parameter_template, priority, enabled)

        Returns:
            Updated rule dictionary or None if not found
        """
        session, driver = self._get_session()
        try:
            # Build SET clause dynamically
            set_parts = []
            params = {"rule_id": rule_id}

            allowed_fields = {"task_id", "description", "source_kind",
                           "parameter_template", "priority", "enabled"}

            for key, value in updates.items():
                if key in allowed_fields:
                    set_parts.append(f"r.{key} = ${key}")
                    params[key] = value

            if not set_parts:
                return self.get_rule(rule_id)

            query = f"""
                MATCH (r:CascadeRule {{rule_id: $rule_id}})
                SET {', '.join(set_parts)}
                RETURN r {{
                    .rule_id, .task_id, .description, .source_kind,
                    .parameter_template, .priority, .enabled, .created_at
                }} as rule
            """

            result = session.run(query, **params)
            record = result.single()

            if record:
                rule = dict(record["rule"])
                if rule.get("created_at"):
                    rule["created_at"] = str(rule["created_at"])
                return rule

            return None

        finally:
            session.close()
            driver.close()

    def enable_rule(self, rule_id: str) -> bool:
        """Enable a cascade rule."""
        result = self.update_rule(rule_id, enabled=True)
        return result is not None

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a cascade rule."""
        result = self.update_rule(rule_id, enabled=False)
        return result is not None

    def delete_rule(self, rule_id: str) -> bool:
        """
        Delete a cascade rule.

        Note: This does not delete TaskRequests that were triggered by this rule.

        Args:
            rule_id: The rule to delete

        Returns:
            True if deleted, False if not found
        """
        session, driver = self._get_session()
        try:
            result = session.run("""
                MATCH (r:CascadeRule {rule_id: $rule_id})
                DELETE r
                RETURN count(*) as deleted
            """, rule_id=rule_id)

            record = result.single()
            return record["deleted"] > 0

        finally:
            session.close()
            driver.close()

    def get_triggered_requests(self, rule_id: str, limit: int = 20) -> List[Dict]:
        """
        Get TaskRequests that were triggered by a rule.

        Args:
            rule_id: The rule identifier
            limit: Maximum requests to return

        Returns:
            List of request summaries
        """
        session, driver = self._get_session()
        try:
            result = session.run("""
                MATCH (req:TaskRequest)-[:TRIGGERED_BY]->(r:CascadeRule {rule_id: $rule_id})
                RETURN req {
                    .request_id, .task_id, .status, .created_at, .finished_at
                } as request
                ORDER BY req.created_at DESC
                LIMIT $limit
            """, rule_id=rule_id, limit=limit)

            requests = []
            for record in result:
                req = dict(record["request"])
                if req.get("created_at"):
                    req["created_at"] = str(req["created_at"])
                if req.get("finished_at"):
                    req["finished_at"] = str(req["finished_at"])
                requests.append(req)

            return requests

        finally:
            session.close()
            driver.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage cascade rules for automatic task triggering"
    )
    parser.add_argument(
        "--database", "-d",
        help="Target database (default: hybridgraph)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # List command
    list_parser = subparsers.add_parser("list", help="List all cascade rules")
    list_parser.add_argument(
        "--enabled-only", "-e",
        action="store_true",
        help="Only show enabled rules"
    )

    # Get command
    get_parser = subparsers.add_parser("get", help="Get a specific rule")
    get_parser.add_argument("rule_id", help="Rule identifier")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new rule")
    create_parser.add_argument("--rule-id", required=True, help="Unique rule identifier")
    create_parser.add_argument("--task", required=True, help="Task ID to create requests for")
    create_parser.add_argument("--description", help="Rule description")
    create_parser.add_argument("--source-kind", help="Match Sources with this kind")
    create_parser.add_argument("--parameters", help="JSON parameter template")
    create_parser.add_argument("--priority", type=int, default=50, help="Request priority")
    create_parser.add_argument("--disabled", action="store_true", help="Create as disabled")

    # Enable command
    enable_parser = subparsers.add_parser("enable", help="Enable a rule")
    enable_parser.add_argument("rule_id", help="Rule identifier")

    # Disable command
    disable_parser = subparsers.add_parser("disable", help="Disable a rule")
    disable_parser.add_argument("rule_id", help="Rule identifier")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a rule")
    delete_parser.add_argument("rule_id", help="Rule identifier")

    # Triggered command
    triggered_parser = subparsers.add_parser("triggered", help="Show requests triggered by a rule")
    triggered_parser.add_argument("rule_id", help="Rule identifier")
    triggered_parser.add_argument("--limit", type=int, default=20, help="Maximum to show")

    args = parser.parse_args()

    manager = CascadeRuleManager(args.database)

    if args.command == "list":
        rules = manager.list_rules(args.enabled_only)
        print(json.dumps(rules, indent=2))

    elif args.command == "get":
        rule = manager.get_rule(args.rule_id)
        if rule:
            print(json.dumps(rule, indent=2))
        else:
            print(f"Rule '{args.rule_id}' not found")
            sys.exit(1)

    elif args.command == "create":
        rule = manager.create_rule(
            rule_id=args.rule_id,
            task_id=args.task,
            description=args.description,
            source_kind=args.source_kind,
            parameter_template=args.parameters,
            priority=args.priority,
            enabled=not args.disabled,
        )
        print(json.dumps(rule, indent=2))

    elif args.command == "enable":
        if manager.enable_rule(args.rule_id):
            print(f"Rule '{args.rule_id}' enabled")
        else:
            print(f"Rule '{args.rule_id}' not found")
            sys.exit(1)

    elif args.command == "disable":
        if manager.disable_rule(args.rule_id):
            print(f"Rule '{args.rule_id}' disabled")
        else:
            print(f"Rule '{args.rule_id}' not found")
            sys.exit(1)

    elif args.command == "delete":
        if manager.delete_rule(args.rule_id):
            print(f"Rule '{args.rule_id}' deleted")
        else:
            print(f"Rule '{args.rule_id}' not found")
            sys.exit(1)

    elif args.command == "triggered":
        requests = manager.get_triggered_requests(args.rule_id, args.limit)
        print(json.dumps(requests, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
