#!/usr/bin/env python3
"""
Runner CLI - Unified command-line interface for the Runner task framework.

This provides access to all Runner functionality:
- Task execution (stack runner)
- Request processing (agent workflow)
- Hybridgraph operations (sync, health, reader, etc.)
- Trigger management (APOC triggers, cascade rules)
- Database bootstrap and migrations

Usage:
    runner stack start <task_id>     Run a task with stack runner
    runner processor                 Start request processor daemon
    runner triggers --install        Install APOC triggers
    runner cascade list              List cascade rules
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


def cmd_processor(args):
    """Handle request processor commands."""
    from runner.processor import daemon
    sys.argv = ["processor"] + args
    daemon.main()


def cmd_triggers(args):
    """Handle APOC trigger commands."""
    from runner.triggers import setup
    sys.argv = ["triggers"] + args
    setup.main()


def cmd_cascade(args):
    """Handle cascade rule commands."""
    from runner.triggers import cascade_rules
    sys.argv = ["cascade"] + args
    cascade_rules.main()


def cmd_mcp(args):
    """Handle MCP server commands."""
    from runner.mcp import server
    sys.argv = ["mcp"] + args
    server.main()


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


def cmd_schema(args):
    """Handle schema migration commands."""
    from runner.db.migrations import add_task_requests
    sys.argv = ["schema"] + args
    add_task_requests.main()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="runner",
        description="Runner - Task execution framework with Neo4j integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  stack      Task execution (LIFO stack runner)
  processor  Request processor daemon (bridges MCP to stack runner)
  triggers   Manage APOC triggers for cascading
  cascade    Manage cascade rules for automatic task creation
  mcp        Start MCP server for agent integration
  schema     Run schema migrations (TaskRequest, etc.)
  sync       Sync jsongraph to hybridgraph
  health     Check hybridgraph health and integrity
  reader     Query and reconstruct documents from hybridgraph
  gc         Run garbage collection on orphaned nodes
  delete     Delete a source from hybridgraph
  migrate    Full migration from jsongraph to hybridgraph
  bootstrap  Initialize databases

Agent Workflow:
  runner schema                      Install TaskRequest schema
  runner triggers --install          Install APOC triggers
  runner processor -v                Start processor daemon (verbose)
  runner cascade list                List cascade rules

Task Execution:
  runner stack start my_task         Run a task directly
  runner stack start upload_dual \\
    --params '{"json_path": "f.json"}'

Hybridgraph Operations:
  runner sync --limit 100            Sync up to 100 documents
  runner health --full               Full health check
  runner reader list                 List all sources
  runner reader get my_source        Reconstruct a document
""",
    )

    parser.add_argument(
        "-v", "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Stack command
    subparsers.add_parser(
        "stack",
        help="Task execution with stack runner",
        add_help=False,
    )

    # Processor command
    subparsers.add_parser(
        "processor",
        help="Request processor daemon",
        add_help=False,
    )

    # Triggers command
    subparsers.add_parser(
        "triggers",
        help="Manage APOC triggers",
        add_help=False,
    )

    # Cascade command
    subparsers.add_parser(
        "cascade",
        help="Manage cascade rules",
        add_help=False,
    )

    # MCP command
    subparsers.add_parser(
        "mcp",
        help="Start MCP server",
        add_help=False,
    )

    # Schema command
    subparsers.add_parser(
        "schema",
        help="Run schema migrations",
        add_help=False,
    )

    # Sync command
    subparsers.add_parser(
        "sync",
        help="Sync jsongraph to hybridgraph",
        add_help=False,
    )

    # Health command
    subparsers.add_parser(
        "health",
        help="Check hybridgraph health",
        add_help=False,
    )

    # Reader command
    subparsers.add_parser(
        "reader",
        help="Query and reconstruct documents",
        add_help=False,
    )

    # GC command
    subparsers.add_parser(
        "gc",
        help="Run garbage collection",
        add_help=False,
    )

    # Delete command
    subparsers.add_parser(
        "delete",
        help="Delete a source",
        add_help=False,
    )

    # Migrate command
    subparsers.add_parser(
        "migrate",
        help="Full migration",
        add_help=False,
    )

    # Bootstrap command
    subparsers.add_parser(
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
        "processor": cmd_processor,
        "triggers": cmd_triggers,
        "cascade": cmd_cascade,
        "mcp": cmd_mcp,
        "schema": cmd_schema,
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
