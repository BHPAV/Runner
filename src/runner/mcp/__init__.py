"""
Runner MCP Server

Provides MCP (Model Context Protocol) tools for agents to:
- Submit task requests to the execution queue
- Monitor request status
- Retrieve execution results
- List available tasks

Usage:
    python -m runner.mcp.server

Or configure in .mcp.json:
    {
        "runner-mcp": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "runner.mcp.server"]
        }
    }
"""

from .server import create_server, main

__all__ = ["create_server", "main"]
