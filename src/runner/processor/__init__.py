"""
Request Processor

Daemon that polls Neo4j for TaskRequest nodes and executes them
via the Stack Runner. This bridges agent-submitted requests to
the task execution system.

Usage:
    python -m runner.processor.daemon

Or:
    from runner.processor import RequestProcessor
    processor = RequestProcessor()
    processor.run_loop()
"""

from .daemon import RequestProcessor, main

__all__ = ["RequestProcessor", "main"]
