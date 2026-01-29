#!/usr/bin/env python3
"""
Runner MCP Server

Exposes tools for agents to submit task requests, check status,
and retrieve results from the Runner task execution system.
"""

import asyncio
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from typing import Any, Optional

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("Error: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

from runner.utils.neo4j import get_driver, get_config


# Server instance
app = Server("runner-mcp")


def get_neo4j_session(database: str = None):
    """Get a Neo4j session for the target database."""
    config = get_config()
    database = database or os.environ.get("NEO4J_DATABASE", config.get("target_db", "hybridgraph"))
    driver = get_driver()
    return driver.session(database=database), driver


def get_sqlite_connection():
    """Get SQLite connection for task definitions."""
    db_path = os.environ.get("RUNNER_DB", os.environ.get("TASK_DB", "./tasks.db"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# Tool Definitions
# =============================================================================

@app.list_tools()
async def list_tools():
    """List available MCP tools."""
    return [
        Tool(
            name="submit_task_request",
            description="""Submit a task request to the execution queue.

The request will be processed by the Runner system. Use get_request_status
to monitor progress and get_task_result to retrieve outputs.

Returns the request_id which can be used to track the request.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task identifier (e.g., 'upload_dual', 'csv_to_json')"
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Task parameters as a JSON object",
                        "default": {}
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Priority (1-1000, higher = sooner). Default: 100",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 1000
                    },
                    "request_id": {
                        "type": "string",
                        "description": "Optional idempotency key. If provided and request exists, returns existing request."
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of request_ids this request depends on"
                    }
                },
                "required": ["task_id"]
            }
        ),
        Tool(
            name="get_request_status",
            description="""Check the status of a task request.

Returns status (pending/claimed/executing/done/failed/cancelled),
timing information, and result reference if complete.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The request ID to check"
                    }
                },
                "required": ["request_id"]
            }
        ),
        Tool(
            name="get_task_result",
            description="""Retrieve the result of a completed task request.

Returns the execution output, context, and any errors.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The request ID to get results for"
                    },
                    "include_trace": {
                        "type": "boolean",
                        "description": "Include full execution trace (can be verbose)",
                        "default": False
                    }
                },
                "required": ["request_id"]
            }
        ),
        Tool(
            name="list_available_tasks",
            description="""List all available tasks that can be submitted.

Returns task IDs with descriptions and parameter schemas.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Optional filter string to match task IDs"
                    },
                    "enabled_only": {
                        "type": "boolean",
                        "description": "Only show enabled tasks",
                        "default": True
                    }
                }
            }
        ),
        Tool(
            name="cancel_request",
            description="""Cancel a pending task request.

Only works for requests in 'pending' or 'blocked' status.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The request ID to cancel"
                    }
                },
                "required": ["request_id"]
            }
        ),
        Tool(
            name="list_pending_requests",
            description="""List pending task requests in the queue.

Useful for monitoring queue depth and understanding what's waiting.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of requests to return",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "claimed", "executing", "done", "failed", "cancelled", "blocked"],
                        "description": "Filter by status (default: pending)"
                    }
                }
            }
        ),
    ]


# =============================================================================
# Tool Implementations
# =============================================================================

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "submit_task_request":
            result = await submit_task_request(**arguments)
        elif name == "get_request_status":
            result = await get_request_status(**arguments)
        elif name == "get_task_result":
            result = await get_task_result(**arguments)
        elif name == "list_available_tasks":
            result = await list_available_tasks(**arguments)
        elif name == "cancel_request":
            result = await cancel_request(**arguments)
        elif name == "list_pending_requests":
            result = await list_pending_requests(**arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        error_result = {
            "error": str(e),
            "tool": name,
            "arguments": arguments
        }
        return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


async def submit_task_request(
    task_id: str,
    parameters: dict = None,
    priority: int = 100,
    request_id: str = None,
    depends_on: list = None
) -> dict:
    """Submit a task request to the Neo4j queue."""
    parameters = parameters or {}
    request_id = request_id or str(uuid.uuid4())
    depends_on = depends_on or []

    # Validate task exists
    conn = get_sqlite_connection()
    try:
        cur = conn.execute(
            "SELECT task_id, task_type, parameters_json FROM tasks WHERE task_id = ?",
            (task_id,)
        )
        task = cur.fetchone()
        if not task:
            return {
                "error": f"Task '{task_id}' not found",
                "suggestion": "Use list_available_tasks to see available tasks"
            }
    finally:
        conn.close()

    # Validate priority
    priority = max(1, min(1000, priority))

    # Determine initial status
    initial_status = "blocked" if depends_on else "pending"

    # Create request in Neo4j
    session, driver = get_neo4j_session()
    try:
        # Check for existing request (idempotency)
        result = session.run("""
            MATCH (r:TaskRequest {request_id: $request_id})
            RETURN r.request_id as request_id, r.status as status, r.created_at as created_at
        """, {"request_id": request_id})
        existing = result.single()

        if existing:
            return {
                "request_id": existing["request_id"],
                "status": existing["status"],
                "created_at": str(existing["created_at"]),
                "message": "Request already exists (idempotent)",
                "is_new": False
            }

        # Create new request
        result = session.run("""
            CREATE (r:TaskRequest {
                request_id: $request_id,
                task_id: $task_id,
                parameters: $parameters,
                status: $status,
                priority: $priority,
                requester: $requester,
                created_at: datetime()
            })
            RETURN r.request_id as request_id, r.status as status, r.created_at as created_at
        """, {
            "request_id": request_id,
            "task_id": task_id,
            "parameters": json.dumps(parameters),
            "status": initial_status,
            "priority": priority,
            "requester": f"mcp:{os.environ.get('USER', 'unknown')}"
        })
        created = result.single()

        # Create dependency relationships
        if depends_on:
            for dep_id in depends_on:
                session.run("""
                    MATCH (r:TaskRequest {request_id: $request_id})
                    MATCH (dep:TaskRequest {request_id: $dep_id})
                    MERGE (r)-[:DEPENDS_ON]->(dep)
                """, {"request_id": request_id, "dep_id": dep_id})

        return {
            "request_id": created["request_id"],
            "status": created["status"],
            "created_at": str(created["created_at"]),
            "task_id": task_id,
            "priority": priority,
            "depends_on": depends_on if depends_on else None,
            "is_new": True
        }

    finally:
        session.close()
        driver.close()


async def get_request_status(request_id: str) -> dict:
    """Get the current status of a task request."""
    session, driver = get_neo4j_session()
    try:
        result = session.run("""
            MATCH (r:TaskRequest {request_id: $request_id})
            OPTIONAL MATCH (r)-[:DEPENDS_ON]->(dep:TaskRequest)
            OPTIONAL MATCH (r)-[:PRODUCED]->(output)
            RETURN r {
                .request_id, .task_id, .status, .priority,
                .requester, .created_at, .claimed_by, .claimed_at,
                .finished_at, .result_ref, .error
            } as request,
            collect(DISTINCT {
                request_id: dep.request_id,
                status: dep.status
            }) as dependencies,
            count(output) as output_count
        """, {"request_id": request_id})

        record = result.single()
        if not record or not record["request"]:
            return {"error": f"Request '{request_id}' not found"}

        req = record["request"]
        deps = [d for d in record["dependencies"] if d["request_id"]]

        response = {
            "request_id": req["request_id"],
            "task_id": req["task_id"],
            "status": req["status"],
            "priority": req["priority"],
            "requester": req["requester"],
            "created_at": str(req["created_at"]) if req["created_at"] else None,
            "claimed_by": req["claimed_by"],
            "claimed_at": str(req["claimed_at"]) if req["claimed_at"] else None,
            "finished_at": str(req["finished_at"]) if req["finished_at"] else None,
            "result_ref": req["result_ref"],
            "error": req["error"],
            "has_outputs": record["output_count"] > 0
        }

        if deps:
            response["dependencies"] = deps
            # Check if blocked by unfinished dependencies
            blocked_by = [d for d in deps if d["status"] != "done"]
            if blocked_by:
                response["blocked_by"] = blocked_by

        return response

    finally:
        session.close()
        driver.close()


async def get_task_result(request_id: str, include_trace: bool = False) -> dict:
    """Get the result of a completed task request."""
    session, driver = get_neo4j_session()
    try:
        # Get request with result reference
        result = session.run("""
            MATCH (r:TaskRequest {request_id: $request_id})
            RETURN r {
                .request_id, .task_id, .status, .result_ref,
                .finished_at, .error
            } as request
        """, {"request_id": request_id})

        record = result.single()
        if not record or not record["request"]:
            return {"error": f"Request '{request_id}' not found"}

        req = record["request"]

        if req["status"] not in ("done", "failed"):
            return {
                "request_id": request_id,
                "status": req["status"],
                "message": f"Request is {req['status']}, not yet complete"
            }

        response = {
            "request_id": req["request_id"],
            "task_id": req["task_id"],
            "status": req["status"],
            "finished_at": str(req["finished_at"]) if req["finished_at"] else None,
            "error": req["error"]
        }

        # If we have a result reference, try to load the execution output
        if req["result_ref"]:
            response["result_ref"] = req["result_ref"]

            # Try to load from runs directory
            runs_dir = os.environ.get("RUNS_DIR", "./runs")
            result_file = os.path.join(runs_dir, f"{req['result_ref']}.json")

            if os.path.exists(result_file):
                with open(result_file) as f:
                    execution_data = json.load(f)

                response["output"] = execution_data.get("final_output")
                response["context"] = execution_data.get("final_context")

                if include_trace:
                    response["trace"] = execution_data.get("trace")
            else:
                response["output_file_missing"] = True

        return response

    finally:
        session.close()
        driver.close()


async def list_available_tasks(filter: str = None, enabled_only: bool = True) -> dict:
    """List available tasks from SQLite."""
    conn = get_sqlite_connection()
    try:
        query = """
            SELECT task_id, task_type, parameters_json, timeout_seconds, enabled
            FROM tasks
        """
        conditions = []
        params = []

        if enabled_only:
            conditions.append("enabled = 1")

        if filter:
            conditions.append("task_id LIKE ?")
            params.append(f"%{filter}%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY task_id"

        cur = conn.execute(query, params)
        tasks = []

        for row in cur.fetchall():
            try:
                default_params = json.loads(row["parameters_json"]) if row["parameters_json"] else {}
            except json.JSONDecodeError:
                default_params = {}

            tasks.append({
                "task_id": row["task_id"],
                "task_type": row["task_type"],
                "default_parameters": default_params,
                "timeout_seconds": row["timeout_seconds"],
                "enabled": bool(row["enabled"])
            })

        return {
            "tasks": tasks,
            "count": len(tasks),
            "filter": filter
        }

    finally:
        conn.close()


async def cancel_request(request_id: str) -> dict:
    """Cancel a pending task request."""
    session, driver = get_neo4j_session()
    try:
        result = session.run("""
            MATCH (r:TaskRequest {request_id: $request_id})
            WHERE r.status IN ['pending', 'blocked']
            SET r.status = 'cancelled',
                r.finished_at = datetime(),
                r.error = 'Cancelled by user'
            RETURN r.request_id as request_id, r.status as status
        """, {"request_id": request_id})

        record = result.single()
        if not record:
            # Check if exists but not cancellable
            check = session.run("""
                MATCH (r:TaskRequest {request_id: $request_id})
                RETURN r.status as status
            """, {"request_id": request_id})
            check_record = check.single()

            if check_record:
                return {
                    "error": f"Cannot cancel request in '{check_record['status']}' status",
                    "request_id": request_id
                }
            else:
                return {"error": f"Request '{request_id}' not found"}

        return {
            "request_id": record["request_id"],
            "status": record["status"],
            "message": "Request cancelled successfully"
        }

    finally:
        session.close()
        driver.close()


async def list_pending_requests(limit: int = 20, status: str = "pending") -> dict:
    """List requests in the queue."""
    session, driver = get_neo4j_session()
    try:
        result = session.run("""
            MATCH (r:TaskRequest {status: $status})
            RETURN r {
                .request_id, .task_id, .status, .priority,
                .requester, .created_at
            } as request
            ORDER BY r.priority DESC, r.created_at ASC
            LIMIT $limit
        """, {"status": status, "limit": limit})

        requests = []
        for record in result:
            req = record["request"]
            requests.append({
                "request_id": req["request_id"],
                "task_id": req["task_id"],
                "priority": req["priority"],
                "requester": req["requester"],
                "created_at": str(req["created_at"]) if req["created_at"] else None
            })

        # Get total count
        count_result = session.run("""
            MATCH (r:TaskRequest {status: $status})
            RETURN count(r) as total
        """, {"status": status})
        total = count_result.single()["total"]

        return {
            "requests": requests,
            "returned": len(requests),
            "total": total,
            "status_filter": status
        }

    finally:
        session.close()
        driver.close()


# =============================================================================
# Server Entry Point
# =============================================================================

def create_server() -> Server:
    """Create and return the MCP server instance."""
    return app


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


def main():
    """Main entry point."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
