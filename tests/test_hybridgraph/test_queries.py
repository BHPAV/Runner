"""Tests for hybridgraph query API."""

import pytest
import sys
sys.path.insert(0, 'src')

from runner.hybridgraph.queries import HybridGraphQuery


class TestHybridGraphQuery:
    """Tests for HybridGraphQuery class."""

    def test_instantiation(self):
        """Should instantiate with default config."""
        query = HybridGraphQuery()
        assert query.uri is not None
        assert query.user is not None
        assert query.database is not None

    def test_custom_config(self):
        """Should accept custom configuration."""
        query = HybridGraphQuery(
            uri="bolt://custom:7687",
            user="custom_user",
            password="custom_pass",
            database="custom_db"
        )
        assert query.uri == "bolt://custom:7687"
        assert query.user == "custom_user"
        assert query.database == "custom_db"

    def test_context_manager_protocol(self):
        """Should implement context manager protocol."""
        query = HybridGraphQuery()
        assert hasattr(query, "__enter__")
        assert hasattr(query, "__exit__")

    def test_has_query_methods(self):
        """Should have all expected query methods."""
        query = HybridGraphQuery()
        expected_methods = [
            "get_document",
            "list_sources",
            "search_content",
            "search_by_key",
            "find_shared_structures",
            "diff_sources",
            "get_source_stats",
            "get_stats",
        ]
        for method in expected_methods:
            assert hasattr(query, method), f"Missing method: {method}"

    def test_close_method(self):
        """Should have close method."""
        query = HybridGraphQuery()
        assert hasattr(query, "close")
        # Should not raise when closing without connection
        query.close()
