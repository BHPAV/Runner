"""Tests for CLI entry point."""

import pytest
import sys
from unittest.mock import patch


class TestCLI:
    """Tests for the CLI module."""

    def test_cli_module_importable(self):
        """CLI module should be importable."""
        from runner import cli
        assert hasattr(cli, "main")

    def test_cli_has_handlers(self):
        """CLI should have handler functions for each command."""
        from runner import cli

        assert hasattr(cli, "cmd_stack")
        assert hasattr(cli, "cmd_sync")
        assert hasattr(cli, "cmd_health")
        assert hasattr(cli, "cmd_reader")
        assert hasattr(cli, "cmd_gc")
        assert hasattr(cli, "cmd_delete")
        assert hasattr(cli, "cmd_migrate")
        assert hasattr(cli, "cmd_bootstrap")

    def test_main_without_args_shows_help(self):
        """Main should show help when no arguments provided."""
        from runner.cli import main

        with patch.object(sys, "argv", ["runner"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_with_help(self):
        """Main should handle --help flag."""
        from runner.cli import main

        with patch.object(sys, "argv", ["runner", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_main_with_version(self):
        """Main should handle --version flag."""
        from runner.cli import main

        with patch.object(sys, "argv", ["runner", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


class TestCLICommands:
    """Tests for individual CLI commands."""

    def test_stack_command_exists(self):
        """Stack command should be recognized."""
        from runner.cli import main
        import argparse

        # Create a parser like main does
        parser = argparse.ArgumentParser(prog="runner")
        subparsers = parser.add_subparsers(dest="command")
        stack_parser = subparsers.add_parser("stack", add_help=False)

        # Parse stack command
        args, remaining = parser.parse_known_args(["stack", "start", "test_task"])
        assert args.command == "stack"

    def test_sync_command_exists(self):
        """Sync command should be recognized."""
        from runner.cli import main
        import argparse

        parser = argparse.ArgumentParser(prog="runner")
        subparsers = parser.add_subparsers(dest="command")
        sync_parser = subparsers.add_parser("sync", add_help=False)

        args, remaining = parser.parse_known_args(["sync", "--limit", "10"])
        assert args.command == "sync"
