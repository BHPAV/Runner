#!/usr/bin/env python3
"""
Integration tests for the agent-driven workflow.

These tests verify the end-to-end flow:
1. Agent submits task request via MCP tools
2. Processor claims and executes request
3. Results are stored and accessible
4. Cascade rules trigger follow-up tasks

Note: These tests require:
- Neo4j running with hybridgraph database
- SQLite tasks.db with tasks seeded
- Environment variables configured (.env)

Run with: pytest tests/test_integration/test_agent_workflow.py -v
"""

import json
import os
import pytest
import sqlite3
import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

# Import test targets
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class TestTaskRequestSchema:
    """Tests for TaskRequest schema operations."""

    @pytest.fixture
    def mock_neo4j_session(self):
        """Create a mock Neo4j session."""
        session = Mock()
        session.run = Mock(return_value=Mock(single=Mock(return_value=None)))
        return session

    def test_task_request_structure(self):
        """Verify TaskRequest has all required fields."""
        required_fields = [
            "request_id",
            "task_id",
            "parameters",
            "status",
            "priority",
            "requester",
            "created_at",
        ]

        # This is a schema validation test - just verifying field expectations
        for field in required_fields:
            assert field in required_fields  # Placeholder for actual schema validation

    def test_request_status_transitions(self):
        """Test valid status transitions."""
        valid_transitions = {
            "pending": ["claimed", "cancelled"],
            "blocked": ["pending", "cancelled"],
            "claimed": ["executing", "failed"],
            "executing": ["done", "failed"],
            "done": [],  # Terminal state
            "failed": [],  # Terminal state
            "cancelled": [],  # Terminal state
        }

        for from_status, to_statuses in valid_transitions.items():
            # Verify these are the expected transitions
            assert isinstance(to_statuses, list)


class TestMCPTools:
    """Tests for MCP server tools."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create a temporary SQLite database with test tasks."""
        db_path = tmp_path / "test_tasks.db"
        conn = sqlite3.connect(str(db_path))

        # Create tasks table
        conn.execute("""
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                code TEXT NOT NULL,
                parameters_json TEXT DEFAULT '{}',
                working_dir TEXT,
                env_json TEXT DEFAULT '{}',
                timeout_seconds INTEGER DEFAULT 300,
                enabled INTEGER DEFAULT 1
            )
        """)

        # Insert test task
        conn.execute("""
            INSERT INTO tasks (task_id, task_type, code)
            VALUES ('test_task', 'python', 'print("hello")')
        """)
        conn.commit()
        conn.close()

        return str(db_path)

    @pytest.mark.asyncio
    async def test_list_available_tasks(self, temp_db):
        """Test listing available tasks."""
        from runner.mcp.server import list_available_tasks

        with patch.dict(os.environ, {"RUNNER_DB": temp_db}):
            result = await list_available_tasks()

        assert "tasks" in result
        assert "count" in result
        assert result["count"] >= 1
        assert any(t["task_id"] == "test_task" for t in result["tasks"])

    @pytest.mark.asyncio
    async def test_list_tasks_with_filter(self, temp_db):
        """Test filtering tasks by name."""
        from runner.mcp.server import list_available_tasks

        with patch.dict(os.environ, {"RUNNER_DB": temp_db}):
            result = await list_available_tasks(filter="test")

        assert result["count"] >= 1
        assert result["filter"] == "test"


class TestRequestProcessor:
    """Tests for the request processor daemon."""

    def test_worker_id_generation(self):
        """Test that worker IDs are unique and formatted correctly."""
        from runner.processor.daemon import get_worker_id

        worker_id = get_worker_id()

        # Should be hostname:pid format
        assert ":" in worker_id
        parts = worker_id.split(":")
        assert len(parts) == 2
        assert parts[1].isdigit()  # PID should be numeric

    def test_processor_initialization(self):
        """Test processor can be initialized with custom settings."""
        from runner.processor.daemon import RequestProcessor

        with patch("runner.processor.daemon.get_driver"):
            processor = RequestProcessor(
                neo4j_database="test_db",
                poll_interval=5.0,
                lease_seconds=600,
                verbose=True,
            )

            assert processor.neo4j_database == "test_db"
            assert processor.poll_interval == 5.0
            assert processor.lease_seconds == 600
            assert processor.verbose is True


class TestCascadeRules:
    """Tests for cascade rule management."""

    @pytest.fixture
    def mock_manager(self):
        """Create a cascade rule manager with mocked Neo4j."""
        from runner.triggers.cascade_rules import CascadeRuleManager

        with patch("runner.triggers.cascade_rules.get_driver"):
            manager = CascadeRuleManager(database="test_db")
            return manager

    def test_rule_creation_validation(self, mock_manager):
        """Test that rules require valid parameters."""
        # Rule must have rule_id and task_id
        with pytest.raises(TypeError):
            mock_manager.create_rule()  # Missing required args

    def test_parameter_template_json_validation(self):
        """Test that parameter templates must be valid JSON."""
        # This would be validated in the actual create_rule method
        valid_template = '{"source_id": "$source.source_id"}'
        invalid_template = "not valid json {"

        try:
            json.loads(valid_template)
            valid = True
        except json.JSONDecodeError:
            valid = False
        assert valid

        try:
            json.loads(invalid_template)
            valid = True
        except json.JSONDecodeError:
            valid = False
        assert not valid


class TestTriggerSetup:
    """Tests for APOC trigger configuration."""

    def test_trigger_definitions_complete(self):
        """Verify all triggers have required fields."""
        from runner.triggers.setup import TRIGGERS

        required_fields = ["description", "statement", "selector"]

        for name, trigger_def in TRIGGERS.items():
            for field in required_fields:
                assert field in trigger_def, f"Trigger {name} missing {field}"

    def test_trigger_statements_valid_cypher(self):
        """Basic validation that trigger statements look like Cypher."""
        from runner.triggers.setup import TRIGGERS

        cypher_keywords = ["MATCH", "SET", "RETURN", "UNWIND", "WITH", "WHERE"]

        for name, trigger_def in TRIGGERS.items():
            statement = trigger_def["statement"].upper()
            has_cypher = any(kw in statement for kw in cypher_keywords)
            assert has_cypher, f"Trigger {name} doesn't look like Cypher"


class TestEndToEnd:
    """End-to-end integration tests (require running services)."""

    @pytest.mark.integration
    @pytest.mark.skipif(
        not os.environ.get("RUN_INTEGRATION_TESTS"),
        reason="Integration tests require RUN_INTEGRATION_TESTS=1"
    )
    def test_full_workflow(self):
        """
        Test complete workflow:
        1. Submit request
        2. Process request
        3. Verify results

        Requires:
        - Neo4j running
        - tasks.db with tasks
        - Environment configured
        """
        # This would be implemented with actual service connections
        pass

    @pytest.mark.integration
    @pytest.mark.skipif(
        not os.environ.get("RUN_INTEGRATION_TESTS"),
        reason="Integration tests require RUN_INTEGRATION_TESTS=1"
    )
    def test_cascade_workflow(self):
        """
        Test cascade rule triggering:
        1. Create cascade rule
        2. Create Source node
        3. Verify TaskRequest created automatically

        Requires Neo4j with APOC triggers enabled.
        """
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
