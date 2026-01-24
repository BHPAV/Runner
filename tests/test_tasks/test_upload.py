"""Tests for upload modules."""

import pytest
from pathlib import Path


class TestUploadModuleFiles:
    """Test that upload module files exist.

    Note: Upload modules are task scripts that execute at import time,
    so we test file existence rather than importability.
    """

    @pytest.fixture
    def upload_dir(self):
        """Get the upload directory path."""
        test_dir = Path(__file__).parent
        src_dir = test_dir.parent.parent / "src" / "runner" / "tasks" / "upload"
        return src_dir

    def test_dual_module_exists(self, upload_dir):
        """Dual upload module file should exist."""
        assert (upload_dir / "dual.py").exists()

    def test_jsongraph_module_exists(self, upload_dir):
        """Jsongraph upload module file should exist."""
        assert (upload_dir / "jsongraph.py").exists()

    def test_batch_module_exists(self, upload_dir):
        """Batch upload module file should exist."""
        assert (upload_dir / "batch.py").exists()


class TestUploadInit:
    """Test upload __init__.py exports."""

    def test_all_exports(self):
        """All expected modules should be in __all__."""
        from runner.tasks import upload
        expected = ["dual", "jsongraph", "batch"]
        for module in expected:
            assert module in upload.__all__

    def test_upload_package_importable(self):
        """The upload package should be importable."""
        from runner.tasks import upload
        assert upload is not None


class TestHashingIntegration:
    """Test that hashing functions are properly imported in upload modules."""

    def test_hashing_functions_available(self):
        """Hashing functions should be importable from utils."""
        from runner.utils.hashing import compute_content_hash, compute_merkle_hash

        # Test basic functionality
        content_hash = compute_content_hash("string", "test_key", "test_value")
        assert content_hash.startswith("c:")
        assert len(content_hash) == 34

        merkle_hash = compute_merkle_hash("object", "root", [content_hash])
        assert merkle_hash.startswith("m:")
        assert len(merkle_hash) == 34

    def test_content_hash_type_safety(self):
        """Content hash should include type prefix for type safety."""
        from runner.utils.hashing import compute_content_hash

        # Same value but different types should produce different hashes
        string_hash = compute_content_hash("string", "value", "true")
        boolean_hash = compute_content_hash("boolean", "value", "true")
        assert string_hash != boolean_hash

        # Same value/type/key should produce same hash
        hash1 = compute_content_hash("number", "count", "42")
        hash2 = compute_content_hash("number", "count", "42")
        assert hash1 == hash2
