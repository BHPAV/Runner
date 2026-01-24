"""Pytest configuration and shared fixtures."""

import os
import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_json():
    """Sample JSON data for testing."""
    return {
        "name": "test",
        "value": 42,
        "nested": {"key": "value"},
        "items": [1, 2, 3]
    }


@pytest.fixture
def neo4j_config():
    """Neo4j configuration for testing."""
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "password"),
        "database": os.environ.get("NEO4J_DATABASE", "jsongraph"),
    }


@pytest.fixture
def skip_without_neo4j(neo4j_config):
    """Skip test if Neo4j is not available."""
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            neo4j_config["uri"],
            auth=(neo4j_config["user"], neo4j_config["password"])
        )
        driver.verify_connectivity()
        driver.close()
    except Exception:
        pytest.skip("Neo4j not available")
