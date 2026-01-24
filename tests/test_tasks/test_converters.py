"""Tests for converter modules - testing package structure and availability."""

import os
import pytest
from pathlib import Path


class TestConverterModuleFiles:
    """Test that converter module files exist.

    Note: Converter modules are task scripts that execute at import time,
    so we test file existence rather than importability.
    """

    @pytest.fixture
    def converters_dir(self):
        """Get the converters directory path."""
        # Navigate from test file to src/runner/tasks/converters
        test_dir = Path(__file__).parent
        src_dir = test_dir.parent.parent / "src" / "runner" / "tasks" / "converters"
        return src_dir

    def test_csv_module_exists(self, converters_dir):
        """CSV converter module file should exist."""
        assert (converters_dir / "csv.py").exists()

    def test_xml_module_exists(self, converters_dir):
        """XML converter module file should exist."""
        assert (converters_dir / "xml.py").exists()

    def test_yaml_module_exists(self, converters_dir):
        """YAML converter module file should exist."""
        assert (converters_dir / "yaml.py").exists()

    def test_markdown_module_exists(self, converters_dir):
        """Markdown converter module file should exist."""
        assert (converters_dir / "markdown.py").exists()

    def test_text_module_exists(self, converters_dir):
        """Text converter module file should exist."""
        assert (converters_dir / "text.py").exists()

    def test_code_module_exists(self, converters_dir):
        """Code converter module file should exist."""
        assert (converters_dir / "code.py").exists()

    def test_python_ast_module_exists(self, converters_dir):
        """Python AST converter module file should exist."""
        assert (converters_dir / "python_ast.py").exists()

    def test_batch_module_exists(self, converters_dir):
        """Batch converter module file should exist."""
        assert (converters_dir / "batch.py").exists()


class TestConvertersInit:
    """Test converters __init__.py exports."""

    def test_all_exports(self):
        """All expected modules should be in __all__."""
        from runner.tasks import converters
        expected = ["csv", "xml", "yaml", "markdown", "text", "code", "python_ast", "batch"]
        for module in expected:
            assert module in converters.__all__

    def test_converters_package_importable(self):
        """The converters package should be importable."""
        from runner.tasks import converters
        assert converters is not None
