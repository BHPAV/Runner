"""
Core task execution engine.

This module provides the main task execution infrastructure:
- stack_runner: LIFO stack-based task execution with monadic context
- runner: Single-file task executor with multi-worker support
- bootstrap: Database initialization and seeding
"""

from runner.core.stack_runner import (
    StackContext,
    get_config,
    create_stack,
    run_stack_to_completion,
    run_stack_step,
)

__all__ = [
    "StackContext",
    "get_config",
    "create_stack",
    "run_stack_to_completion",
    "run_stack_step",
]
