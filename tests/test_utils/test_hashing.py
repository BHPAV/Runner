"""Tests for hashing utilities."""

import pytest
import sys
sys.path.insert(0, 'src')

from runner.utils.hashing import compute_content_hash, compute_merkle_hash


class TestComputeContentHash:
    """Tests for compute_content_hash function."""

    def test_returns_correct_format(self):
        """Hash should have 'c:' prefix and 32 hex chars."""
        result = compute_content_hash("string", "name", "Alice")
        assert result.startswith("c:")
        assert len(result) == 34  # 2 prefix + 32 hex

    def test_deterministic(self):
        """Same inputs should produce same hash."""
        hash1 = compute_content_hash("string", "name", "Alice")
        hash2 = compute_content_hash("string", "name", "Alice")
        assert hash1 == hash2

    def test_different_values_different_hashes(self):
        """Different values should produce different hashes."""
        hash1 = compute_content_hash("string", "name", "Alice")
        hash2 = compute_content_hash("string", "name", "Bob")
        assert hash1 != hash2

    def test_different_kinds_different_hashes(self):
        """Different kinds should produce different hashes."""
        hash1 = compute_content_hash("string", "value", "42")
        hash2 = compute_content_hash("number", "value", "42")
        assert hash1 != hash2

    def test_null_value(self):
        """Should handle null value strings."""
        result = compute_content_hash("null", "field", "null")
        assert result.startswith("c:")
        assert len(result) == 34


class TestComputeMerkleHash:
    """Tests for compute_merkle_hash function."""

    def test_returns_correct_format(self):
        """Hash should have 'm:' prefix and 32 hex chars."""
        result = compute_merkle_hash("object", "root", [])
        assert result.startswith("m:")
        assert len(result) == 34

    def test_deterministic(self):
        """Same inputs should produce same hash."""
        children = ["c:abc123", "c:def456"]
        hash1 = compute_merkle_hash("object", "data", children)
        hash2 = compute_merkle_hash("object", "data", children)
        assert hash1 == hash2

    def test_order_independent(self):
        """Child order should not affect hash (sorted internally)."""
        hash1 = compute_merkle_hash("object", "root", ["c:aaa", "c:bbb"])
        hash2 = compute_merkle_hash("object", "root", ["c:bbb", "c:aaa"])
        assert hash1 == hash2

    def test_empty_children(self):
        """Should handle empty child list."""
        result = compute_merkle_hash("array", "items", [])
        assert result.startswith("m:")
        assert len(result) == 34

    def test_different_kinds_different_hashes(self):
        """Object vs array should produce different hashes."""
        hash1 = compute_merkle_hash("object", "root", [])
        hash2 = compute_merkle_hash("array", "root", [])
        assert hash1 != hash2
