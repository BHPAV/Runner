#!/usr/bin/env python3
"""Migration: add request_id column to task_queue (idempotent)."""

import sqlite3
import uuid
import os
import sys


def migrate(db_path: str):
    """Add request_id column to task_queue table.

    This migration is idempotent - running it multiple times is safe.
    Existing rows without request_id will be assigned new UUIDs.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Check if already migrated
    cur = conn.execute("PRAGMA table_info(task_queue)")
    columns = [r["name"] for r in cur.fetchall()]

    if "request_id" in columns:
        print("Already migrated - request_id column exists")
        conn.close()
        return

    print(f"Migrating database: {db_path}")

    # Add column (SQLite doesn't support NOT NULL for ALTER TABLE ADD)
    conn.execute("ALTER TABLE task_queue ADD COLUMN request_id TEXT")
    print("Added request_id column")

    # Populate existing rows with UUIDs
    cur = conn.execute("SELECT queue_id FROM task_queue WHERE request_id IS NULL")
    rows = cur.fetchall()

    updated = 0
    for row in rows:
        conn.execute(
            "UPDATE task_queue SET request_id = ? WHERE queue_id = ?",
            (str(uuid.uuid4()), row["queue_id"])
        )
        updated += 1

    if updated > 0:
        print(f"Assigned request_id to {updated} existing rows")

    # Create unique index
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_request_id ON task_queue(request_id)")
    print("Created unique index on request_id")

    conn.commit()
    conn.close()
    print("Migration complete")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TASK_DB", "./tasks.db")
    migrate(db_path)
