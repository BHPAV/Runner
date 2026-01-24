"""
Neo4j connection utilities.

Provides standardized configuration loading and driver management
for connecting to Neo4j databases.
"""

import os
from typing import Dict, Any, Optional

__all__ = ["get_config", "get_driver", "get_session"]


def get_config() -> Dict[str, str]:
    """
    Load Neo4j configuration from environment variables.

    Returns:
        Dictionary with uri, user, password, source_db, and target_db
    """
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "password"),
        "source_db": os.environ.get("SOURCE_DB", "jsongraph"),
        "target_db": os.environ.get("TARGET_DB", "hybridgraph"),
    }


def get_driver(uri: Optional[str] = None, user: Optional[str] = None,
               password: Optional[str] = None):
    """
    Create a Neo4j driver instance.

    Args:
        uri: Neo4j bolt URI (uses NEO4J_URI env var if not provided)
        user: Username (uses NEO4J_USER env var if not provided)
        password: Password (uses NEO4J_PASSWORD env var if not provided)

    Returns:
        Neo4j driver instance

    Raises:
        ImportError: If neo4j package is not installed
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise ImportError(
            "neo4j package not installed. Run: pip install neo4j"
        )

    config = get_config()
    return GraphDatabase.driver(
        uri or config["uri"],
        auth=(user or config["user"], password or config["password"])
    )


def get_session(driver, database: Optional[str] = None):
    """
    Create a Neo4j session from a driver.

    Args:
        driver: Neo4j driver instance
        database: Database name (uses SOURCE_DB env var if not provided)

    Returns:
        Neo4j session
    """
    if database is None:
        database = os.environ.get("NEO4J_DATABASE", "jsongraph")
    return driver.session(database=database)
