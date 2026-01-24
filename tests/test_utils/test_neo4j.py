"""Tests for Neo4j utilities."""

import os
import pytest
import sys
sys.path.insert(0, 'src')

from runner.utils.neo4j import get_config


class TestGetConfig:
    """Tests for get_config function."""

    def test_returns_dict(self):
        """Should return a dictionary."""
        config = get_config()
        assert isinstance(config, dict)

    def test_has_required_keys(self):
        """Should have all required configuration keys."""
        config = get_config()
        required_keys = ["uri", "user", "password", "source_db", "target_db"]
        for key in required_keys:
            assert key in config, f"Missing key: {key}"

    def test_default_values(self):
        """Should have sensible defaults."""
        # Clear env vars temporarily
        original = {k: os.environ.pop(k, None) for k in
                   ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "SOURCE_DB", "TARGET_DB"]}
        try:
            config = get_config()
            assert config["uri"] == "bolt://localhost:7687"
            assert config["user"] == "neo4j"
            assert config["source_db"] == "jsongraph"
            assert config["target_db"] == "hybridgraph"
        finally:
            # Restore env vars
            for k, v in original.items():
                if v is not None:
                    os.environ[k] = v

    def test_reads_from_environment(self):
        """Should read values from environment variables."""
        os.environ["NEO4J_URI"] = "bolt://custom:7687"
        try:
            config = get_config()
            assert config["uri"] == "bolt://custom:7687"
        finally:
            del os.environ["NEO4J_URI"]
