"""Open the database read-only and run queries with a time limit.

Two layers stop writes, even if the SQL validator has a bug:
1. The file is opened with the SQLite URI option ``mode=ro`` (read-only).
2. ``PRAGMA query_only = ON`` makes SQLite refuse any statement that changes data.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# SQLite calls the progress handler every N virtual-machine instructions.
# 1,000 is frequent enough to stop a runaway query within milliseconds.
PROGRESS_HANDLER_STEPS = 1_000


class QueryTimeoutError(Exception):
    """Raised when a query runs longer than the allowed time."""


@dataclass(frozen=True)
class QueryResult:
    """Column names and rows returned by a query."""

    columns: list[str]
    rows: list[tuple[Any, ...]]


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the SQLite database at db_path in read-only mode.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    if not db_path.is_file():
        raise FileNotFoundError(
            f"Database not found: {db_path}. Build it first with: python -m askdata.db.loader"
        )

    # as_uri() gives "file:///D:/.../askdata.db" with special characters escaped.
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    # check_same_thread=False lets Streamlit reuse the connection across reruns.
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.execute("PRAGMA query_only = ON")
    return connection


def run_query(connection: sqlite3.Connection, sql: str, timeout_seconds: float) -> QueryResult:
    """Run one SQL query and return its columns and rows.

    The query is stopped if it runs longer than timeout_seconds.

    Raises:
        QueryTimeoutError: If the time limit is reached.
        sqlite3.Error: For any other SQL error (syntax, unknown column, ...).
    """
    deadline = time.monotonic() + timeout_seconds

    def stop_if_too_slow() -> int:
        """Progress handler: a non-zero return value makes SQLite abort the query."""
        if time.monotonic() > deadline:
            return 1
        return 0

    connection.set_progress_handler(stop_if_too_slow, PROGRESS_HANDLER_STEPS)
    try:
        cursor = connection.execute(sql)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description or []]
    except sqlite3.OperationalError as error:
        # SQLite reports an aborted query as "interrupted".
        if "interrupted" in str(error) and time.monotonic() > deadline:
            raise QueryTimeoutError(
                f"Query took longer than {timeout_seconds} seconds and was stopped."
            ) from error
        raise
    finally:
        # Remove the handler so it does not affect later queries.
        connection.set_progress_handler(None, 0)

    return QueryResult(columns=columns, rows=rows)
