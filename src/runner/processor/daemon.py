#!/usr/bin/env python3
"""
Request Processor Daemon

Polls Neo4j for pending TaskRequest nodes and executes them
via the Stack Runner. This bridges agent-submitted requests
to the task execution system.

Usage:
    python -m runner.processor.daemon [options]

Options:
    --poll-interval SECONDS  How often to check for new requests (default: 2)
    --lease-seconds SECONDS  Task lease duration (default: 300)
    --single                 Process one request and exit
    --verbose, -v            Enable verbose output
"""

import argparse
import json
import os
import signal
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from runner.utils.neo4j import get_driver, get_config
from runner.core.stack_runner import (
    create_stack,
    run_stack_to_completion,
    get_stack_info,
    save_stack_output,
)


def utc_now() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def get_worker_id() -> str:
    """Generate a unique worker ID."""
    return f"{socket.gethostname()}:{os.getpid()}"


class RequestProcessor:
    """
    Processes TaskRequest nodes from Neo4j via the Stack Runner.

    The processor:
    1. Claims pending requests atomically
    2. Executes them via create_stack + run_stack_to_completion
    3. Updates request status with results
    4. Links output to the request node
    """

    def __init__(
        self,
        neo4j_database: str = None,
        sqlite_db_path: str = None,
        runs_dir: str = None,
        poll_interval: float = 2.0,
        lease_seconds: int = 300,
        verbose: bool = False,
    ):
        """
        Initialize the processor.

        Args:
            neo4j_database: Target Neo4j database (default: hybridgraph)
            sqlite_db_path: Path to SQLite tasks database
            runs_dir: Directory for execution output files
            poll_interval: Seconds between polling for requests
            lease_seconds: Task lease duration for stack runner
            verbose: Enable verbose logging
        """
        config = get_config()

        self.neo4j_database = neo4j_database or os.environ.get(
            "NEO4J_DATABASE", config.get("target_db", "hybridgraph")
        )
        self.sqlite_db_path = sqlite_db_path or os.environ.get(
            "RUNNER_DB", os.environ.get("TASK_DB", "./tasks.db")
        )
        self.runs_dir = runs_dir or os.environ.get("RUNS_DIR", "./runs")
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.verbose = verbose

        self.worker_id = get_worker_id()
        self.shutdown_requested = False
        self.requests_processed = 0
        self.requests_failed = 0

        # Stack runner config
        self.stack_config = {
            "db_path": self.sqlite_db_path,
            "lease_seconds": self.lease_seconds,
            "runs_dir": self.runs_dir,
        }

        if self.verbose:
            print(f"RequestProcessor initialized")
            print(f"  Worker ID: {self.worker_id}")
            print(f"  Neo4j DB: {self.neo4j_database}")
            print(f"  SQLite DB: {self.sqlite_db_path}")
            print(f"  Runs dir: {self.runs_dir}")
            print(f"  Poll interval: {self.poll_interval}s")

    def _get_neo4j_session(self):
        """Get a Neo4j session."""
        driver = get_driver()
        return driver.session(database=self.neo4j_database), driver

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        """Get a SQLite connection."""
        conn = sqlite3.connect(self.sqlite_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def claim_request(self) -> Optional[dict]:
        """
        Atomically claim the next pending request.

        Uses compare-and-swap to ensure only one worker claims each request.
        Also checks that all dependencies are satisfied.

        Returns:
            Request dict if claimed, None if no requests available
        """
        session, driver = self._get_neo4j_session()
        try:
            # Atomic claim: find pending request with no unsatisfied dependencies
            result = session.run("""
                MATCH (r:TaskRequest)
                WHERE r.status = 'pending'
                AND NOT EXISTS {
                    MATCH (r)-[:DEPENDS_ON]->(dep:TaskRequest)
                    WHERE dep.status <> 'done'
                }
                WITH r
                ORDER BY r.priority DESC, r.created_at ASC
                LIMIT 1
                SET r.status = 'claimed',
                    r.claimed_by = $worker_id,
                    r.claimed_at = datetime()
                RETURN r {
                    .request_id, .task_id, .parameters, .priority,
                    .requester, .created_at
                } as request
            """, worker_id=self.worker_id)

            record = result.single()
            if record and record["request"]:
                req = record["request"]
                return {
                    "request_id": req["request_id"],
                    "task_id": req["task_id"],
                    "parameters": json.loads(req["parameters"]) if req["parameters"] else {},
                    "priority": req["priority"],
                    "requester": req["requester"],
                    "created_at": str(req["created_at"]) if req["created_at"] else None,
                }
            return None

        finally:
            session.close()
            driver.close()

    def mark_executing(self, request_id: str):
        """Mark a request as executing."""
        session, driver = self._get_neo4j_session()
        try:
            session.run("""
                MATCH (r:TaskRequest {request_id: $request_id})
                SET r.status = 'executing'
            """, request_id=request_id)
        finally:
            session.close()
            driver.close()

    def mark_done(self, request_id: str, result_ref: str):
        """Mark a request as successfully completed."""
        session, driver = self._get_neo4j_session()
        try:
            session.run("""
                MATCH (r:TaskRequest {request_id: $request_id})
                SET r.status = 'done',
                    r.finished_at = datetime(),
                    r.result_ref = $result_ref
            """, request_id=request_id, result_ref=result_ref)
        finally:
            session.close()
            driver.close()

    def mark_failed(self, request_id: str, error: str):
        """Mark a request as failed."""
        session, driver = self._get_neo4j_session()
        try:
            session.run("""
                MATCH (r:TaskRequest {request_id: $request_id})
                SET r.status = 'failed',
                    r.finished_at = datetime(),
                    r.error = $error
            """, request_id=request_id, error=error[:2000])  # Truncate long errors
        finally:
            session.close()
            driver.close()

    def resolve_blocked_requests(self, completed_request_id: str):
        """
        Check if completing this request unblocks any others.

        When a request completes, any requests that depend only on it
        (and other completed requests) should be moved to 'pending'.
        """
        session, driver = self._get_neo4j_session()
        try:
            result = session.run("""
                MATCH (waiting:TaskRequest)-[:DEPENDS_ON]->(completed:TaskRequest {request_id: $request_id})
                WHERE waiting.status = 'blocked'
                AND NOT EXISTS {
                    MATCH (waiting)-[:DEPENDS_ON]->(other:TaskRequest)
                    WHERE other.status <> 'done'
                }
                SET waiting.status = 'pending'
                RETURN waiting.request_id as unblocked
            """, request_id=completed_request_id)

            unblocked = [r["unblocked"] for r in result]
            if unblocked and self.verbose:
                print(f"  Unblocked {len(unblocked)} dependent requests: {unblocked}")

            return unblocked

        finally:
            session.close()
            driver.close()

    def execute_request(self, request: dict) -> dict:
        """
        Execute a request via the Stack Runner.

        Args:
            request: Request dict with task_id and parameters

        Returns:
            Stack info dict with execution results
        """
        conn = self._get_sqlite_conn()
        try:
            # Create stack for this request
            stack_result = create_stack(
                conn,
                request["task_id"],
                request["parameters"],
                request["request_id"]  # Use request_id for idempotency
            )

            if self.verbose:
                print(f"  Created stack: {stack_result['stack_id']}")

            # Run to completion
            stack_info = run_stack_to_completion(
                conn,
                stack_result["stack_id"],
                self.stack_config,
                verbose=self.verbose
            )

            # Save output
            output_path = save_stack_output(
                self.runs_dir,
                stack_result["stack_id"],
                stack_info
            )

            if self.verbose:
                print(f"  Stack completed: {stack_info['status']}")
                print(f"  Output: {output_path}")

            # Add output path to stack info
            stack_info["output_path"] = output_path

            return stack_info

        finally:
            conn.close()

    def process_one(self) -> bool:
        """
        Process a single request if available.

        Returns:
            True if a request was processed, False if queue was empty
        """
        # Try to claim a request
        request = self.claim_request()
        if not request:
            return False

        request_id = request["request_id"]

        if self.verbose:
            print(f"\nProcessing request: {request_id}")
            print(f"  Task: {request['task_id']}")
            print(f"  Priority: {request['priority']}")

        # Mark as executing
        self.mark_executing(request_id)

        try:
            # Execute via stack runner
            result = self.execute_request(request)

            if result["status"] == "done":
                # Use stack_id as result reference (matches output file naming)
                result_ref = f"stack_{result['stack_id'][:8]}"
                self.mark_done(request_id, result_ref)
                self.requests_processed += 1

                if self.verbose:
                    print(f"  SUCCESS: {result_ref}")
            else:
                # Stack completed but with error/failure status
                error_msg = result.get("error") or f"Stack ended with status: {result['status']}"
                self.mark_failed(request_id, error_msg)
                self.requests_failed += 1

                if self.verbose:
                    print(f"  FAILED: {error_msg}")

            # Check if this unblocks other requests
            self.resolve_blocked_requests(request_id)

        except Exception as e:
            # Execution error
            self.mark_failed(request_id, str(e))
            self.requests_failed += 1

            if self.verbose:
                print(f"  ERROR: {e}")
                import traceback
                traceback.print_exc()

        return True

    def run_loop(self):
        """
        Main processing loop.

        Polls for requests and processes them until shutdown is requested.
        """
        print(f"Request processor starting (worker: {self.worker_id})")
        print(f"Press Ctrl+C to stop")
        print()

        # Set up signal handlers
        def handle_signal(signum, frame):
            print("\nShutdown requested...")
            self.shutdown_requested = True

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        idle_count = 0

        while not self.shutdown_requested:
            try:
                processed = self.process_one()

                if processed:
                    idle_count = 0
                else:
                    idle_count += 1
                    # Only print idle message occasionally
                    if idle_count == 1 and self.verbose:
                        print("Waiting for requests...")
                    time.sleep(self.poll_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error in processing loop: {e}")
                if self.verbose:
                    import traceback
                    traceback.print_exc()
                time.sleep(self.poll_interval)

        print()
        print(f"Processor stopped")
        print(f"  Requests processed: {self.requests_processed}")
        print(f"  Requests failed: {self.requests_failed}")

    def get_stats(self) -> dict:
        """Get current processor statistics."""
        session, driver = self._get_neo4j_session()
        try:
            result = session.run("""
                MATCH (r:TaskRequest)
                RETURN r.status as status, count(r) as count
            """)

            stats = {
                "worker_id": self.worker_id,
                "processed": self.requests_processed,
                "failed": self.requests_failed,
                "queue": {}
            }

            for record in result:
                stats["queue"][record["status"]] = record["count"]

            return stats

        finally:
            session.close()
            driver.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Process TaskRequest nodes from Neo4j"
    )
    parser.add_argument(
        "--poll-interval", "-i",
        type=float,
        default=2.0,
        help="Seconds between polling for requests"
    )
    parser.add_argument(
        "--lease-seconds", "-l",
        type=int,
        default=300,
        help="Task lease duration in seconds"
    )
    parser.add_argument(
        "--database", "-d",
        help="Neo4j database (default: hybridgraph)"
    )
    parser.add_argument(
        "--db-path",
        help="SQLite database path"
    )
    parser.add_argument(
        "--runs-dir",
        help="Directory for execution outputs"
    )
    parser.add_argument(
        "--single", "-1",
        action="store_true",
        help="Process one request and exit"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show queue statistics and exit"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    args = parser.parse_args()

    processor = RequestProcessor(
        neo4j_database=args.database,
        sqlite_db_path=args.db_path,
        runs_dir=args.runs_dir,
        poll_interval=args.poll_interval,
        lease_seconds=args.lease_seconds,
        verbose=args.verbose,
    )

    if args.stats:
        stats = processor.get_stats()
        print(json.dumps(stats, indent=2))
        return

    if args.single:
        processed = processor.process_one()
        if not processed:
            print("No requests to process")
        return

    processor.run_loop()


if __name__ == "__main__":
    main()
