#!/usr/bin/env python3
"""
Runner CLI - Unified command-line interface for the Runner task framework.

This provides access to all Runner functionality:
- Task execution (stack runner)
- Hybridgraph operations (sync, health, reader, etc.)
- Database bootstrap

Usage:
    runner stack start <task_id>     Run a task with stack runner
    runner sync                      Sync jsongraph to hybridgraph
    runner health                    Check hybridgraph health
    runner reader list               List hybridgraph sources
    runner reader get <source_id>    Reconstruct a document
    runner gc                        Run garbage collection
"""

import argparse
import sys


def cmd_stack(args):
    """Handle stack runner commands."""
    from runner.core import stack_runner
    # Delegate to stack_runner's main with modified argv
    sys.argv = ["stack-runner"] + args
    stack_runner.main()


def cmd_sync(args):
    """Handle sync commands."""
    from runner.hybridgraph import sync
    sys.argv = ["sync"] + args
    sync.main()


def cmd_health(args):
    """Handle health check commands."""
    from runner.hybridgraph import health
    sys.argv = ["health"] + args
    health.main()


def cmd_reader(args):
    """Handle reader commands."""
    from runner.hybridgraph import reader
    sys.argv = ["reader"] + args
    reader.main()


def cmd_gc(args):
    """Handle garbage collection commands."""
    from runner.hybridgraph import gc
    sys.argv = ["gc"] + args
    gc.main()


def cmd_delete(args):
    """Handle source deletion commands."""
    from runner.hybridgraph import delete
    sys.argv = ["delete"] + args
    delete.main()


def cmd_migrate(args):
    """Handle full migration commands."""
    from runner.hybridgraph import migrate
    sys.argv = ["migrate"] + args
    migrate.main()


def cmd_bootstrap(args):
    """Handle database bootstrap commands."""
    from runner.core import bootstrap
    sys.argv = ["bootstrap"] + args
    bootstrap.main()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="runner",
        description="Runner - Task execution framework with Neo4j integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  stack     Task execution (LIFO stack runner)
  sync      Sync jsongraph to hybridgraph
  health    Check hybridgraph health and integrity
  reader    Query and reconstruct documents from hybridgraph
  gc        Run garbage collection on orphaned nodes
  delete    Delete a source from hybridgraph
  migrate   Full migration from jsongraph to hybridgraph
  bootstrap Initialize databases

Examples:
  runner stack start my_task          Run a task
  runner sync --limit 100             Sync up to 100 documents
  runner health --full                Full health check
  runner reader list                  List all sources
  runner reader get my_source         Reconstruct a document
""",
    )

    parser.add_argument(
        "-v", "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Stack command
    stack_parser = subparsers.add_parser(
        "stack",
        help="Task execution with stack runner",
        add_help=False,
    )

    # Sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync jsongraph to hybridgraph",
        add_help=False,
    )

    # Health command
    health_parser = subparsers.add_parser(
        "health",
        help="Check hybridgraph health",
        add_help=False,
    )

    # Reader command
    reader_parser = subparsers.add_parser(
        "reader",
        help="Query and reconstruct documents",
        add_help=False,
    )

    # GC command
    gc_parser = subparsers.add_parser(
        "gc",
        help="Run garbage collection",
        add_help=False,
    )

    # Delete command
    delete_parser = subparsers.add_parser(
        "delete",
        help="Delete a source",
        add_help=False,
    )

    # Migrate command
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Full migration",
        add_help=False,
    )

    # Bootstrap command
    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Initialize databases",
        add_help=False,
    )

    # Parse only the first argument to get the command
    args, remaining = parser.parse_known_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Dispatch to appropriate handler
    handlers = {
        "stack": cmd_stack,
        "sync": cmd_sync,
        "health": cmd_health,
        "reader": cmd_reader,
        "gc": cmd_gc,
        "delete": cmd_delete,
        "migrate": cmd_migrate,
        "bootstrap": cmd_bootstrap,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(remaining)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
