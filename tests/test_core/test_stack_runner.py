"""Tests for stack_runner module."""

import json
import os
import pytest
import sqlite3
import tempfile
from pathlib import Path

from runner.core.stack_runner import (
    StackContext,
    get_config,
    load_json,
    merge_dicts,
    parse_task_result,
    TaskResult,
)


class TestStackContext:
    """Tests for StackContext class."""

    def test_default_initialization(self):
        """Should initialize with empty defaults."""
        ctx = StackContext()
        assert ctx.variables == {}
        assert ctx.outputs == []
        assert ctx.decisions == []
        assert ctx.errors == []
        assert ctx.metadata == {}

    def test_initialization_with_values(self):
        """Should accept initial values."""
        ctx = StackContext(
            variables={"key": "value"},
            outputs=[{"result": 1}],
            decisions=["Decision 1"],
            errors=["Error 1"],
            metadata={"source": "test"},
        )
        assert ctx.variables == {"key": "value"}
        assert ctx.outputs == [{"result": 1}]
        assert ctx.decisions == ["Decision 1"]
        assert ctx.errors == ["Error 1"]
        assert ctx.metadata == {"source": "test"}

    def test_bind_adds_variables(self):
        """Bind should merge new variables."""
        ctx = StackContext(variables={"a": 1})
        new_ctx = ctx.bind({"variables": {"b": 2}})
        assert new_ctx.variables == {"a": 1, "b": 2}
        # Original should be unchanged
        assert ctx.variables == {"a": 1}

    def test_bind_accumulates_outputs(self):
        """Bind should append outputs."""
        ctx = StackContext(outputs=[{"first": 1}])
        new_ctx = ctx.bind({"output": {"second": 2}})
        assert len(new_ctx.outputs) == 2
        assert new_ctx.outputs[0] == {"first": 1}
        assert new_ctx.outputs[1] == {"second": 2}

    def test_bind_accumulates_decisions(self):
        """Bind should extend decisions."""
        ctx = StackContext(decisions=["Decision 1"])
        new_ctx = ctx.bind({"decisions": ["Decision 2", "Decision 3"]})
        assert new_ctx.decisions == ["Decision 1", "Decision 2", "Decision 3"]

    def test_bind_accumulates_errors(self):
        """Bind should extend errors."""
        ctx = StackContext(errors=["Error 1"])
        new_ctx = ctx.bind({"errors": ["Error 2"]})
        assert new_ctx.errors == ["Error 1", "Error 2"]

    def test_to_dict(self):
        """Should convert to dictionary."""
        ctx = StackContext(
            variables={"key": "value"},
            outputs=[{"result": 1}],
        )
        d = ctx.to_dict()
        assert d["variables"] == {"key": "value"}
        assert d["outputs"] == [{"result": 1}]

    def test_from_dict(self):
        """Should create from dictionary."""
        d = {
            "variables": {"key": "value"},
            "outputs": [{"result": 1}],
            "decisions": ["Decision 1"],
            "errors": [],
            "metadata": {},
        }
        ctx = StackContext.from_dict(d)
        assert ctx.variables == {"key": "value"}
        assert ctx.outputs == [{"result": 1}]
        assert ctx.decisions == ["Decision 1"]


class TestGetConfig:
    """Tests for get_config function."""

    def test_default_values(self):
        """Should return default values when env vars not set."""
        # Save current env
        orig_db = os.environ.get("TASK_DB")
        orig_runs = os.environ.get("RUNS_DIR")
        orig_lease = os.environ.get("TASK_LEASE_SECONDS")

        # Clear env vars
        for key in ["TASK_DB", "RUNS_DIR", "TASK_LEASE_SECONDS"]:
            if key in os.environ:
                del os.environ[key]

        try:
            config = get_config()
            assert config["db_path"] == "./tasks.db"
            assert config["runs_dir"] == "./runs"
            assert config["lease_seconds"] == 300
        finally:
            # Restore env
            if orig_db:
                os.environ["TASK_DB"] = orig_db
            if orig_runs:
                os.environ["RUNS_DIR"] = orig_runs
            if orig_lease:
                os.environ["TASK_LEASE_SECONDS"] = orig_lease

    def test_custom_values_from_env(self):
        """Should read values from environment variables."""
        os.environ["TASK_DB"] = "/custom/path/tasks.db"
        os.environ["RUNS_DIR"] = "/custom/runs"
        os.environ["TASK_LEASE_SECONDS"] = "600"

        try:
            config = get_config()
            assert config["db_path"] == "/custom/path/tasks.db"
            assert config["runs_dir"] == "/custom/runs"
            assert config["lease_seconds"] == 600
        finally:
            del os.environ["TASK_DB"]
            del os.environ["RUNS_DIR"]
            del os.environ["TASK_LEASE_SECONDS"]


class TestLoadJson:
    """Tests for load_json function."""

    def test_valid_json(self):
        """Should parse valid JSON."""
        result = load_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_empty_string(self):
        """Should return default for empty string."""
        result = load_json("")
        assert result == {}

    def test_none(self):
        """Should return default for None."""
        result = load_json(None)
        assert result == {}

    def test_invalid_json(self):
        """Should return default for invalid JSON."""
        result = load_json("not valid json")
        assert result == {}

    def test_custom_default(self):
        """Should return custom default when provided."""
        result = load_json("invalid", default={"fallback": True})
        assert result == {"fallback": True}


class TestMergeDicts:
    """Tests for merge_dicts function."""

    def test_merge_two_dicts(self):
        """Should merge two dictionaries."""
        result = merge_dicts({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_later_overrides_earlier(self):
        """Later dicts should override earlier ones."""
        result = merge_dicts({"a": 1}, {"a": 2})
        assert result == {"a": 2}

    def test_handles_none(self):
        """Should handle None values."""
        result = merge_dicts({"a": 1}, None, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_empty_input(self):
        """Should return empty dict for no inputs."""
        result = merge_dicts()
        assert result == {}


class TestParseTaskResult:
    """Tests for parse_task_result function."""

    def test_valid_task_result(self):
        """Should parse valid task result JSON."""
        stdout = '{"__task_result__": true, "output": {"status": "done"}, "variables": {"count": 5}}'
        result = parse_task_result(stdout)
        assert result is not None
        assert result.output == {"status": "done"}
        assert result.variables == {"count": 5}

    def test_result_in_multiline_output(self):
        """Should find task result in multiline output."""
        stdout = """
Processing...
Step 1 complete
{"__task_result__": true, "output": "success", "decisions": ["Completed all steps"]}
"""
        result = parse_task_result(stdout)
        assert result is not None
        assert result.output == "success"
        assert result.decisions == ["Completed all steps"]

    def test_no_task_result_marker(self):
        """Should return basic result for output without marker."""
        stdout = '{"regular": "json"}'
        result = parse_task_result(stdout)
        assert result is not None
        # Without __task_result__ marker, output is treated as plain text
        assert result.output is None or result.output == '{"regular": "json"}'

    def test_plain_text_output(self):
        """Should handle plain text output."""
        stdout = "Just some text output"
        result = parse_task_result(stdout)
        assert result is not None
        assert result.output == "Just some text output"

    def test_empty_output(self):
        """Should handle empty output."""
        result = parse_task_result("")
        assert result is not None
        assert result.output is None

    def test_abort_flag(self):
        """Should parse abort flag."""
        stdout = '{"__task_result__": true, "abort": true, "errors": ["Critical error"]}'
        result = parse_task_result(stdout)
        assert result is not None
        assert result.abort is True
        assert result.errors == ["Critical error"]
