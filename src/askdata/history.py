"""Log every asked question to a small SQLite file, separate from the data.

The data database is opened read-only, so the history needs its own file
(data/history.db by default). It is written by the app and the command line.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported only for type hints, to avoid a circular import with the pipeline.
    from askdata.nl2sql.pipeline import PipelineResult

CREATE_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at         TEXT NOT NULL,     -- UTC time, ISO 8601
    question         TEXT NOT NULL,
    sql              TEXT,
    status           TEXT NOT NULL,
    latency_seconds  REAL NOT NULL,
    row_count        INTEGER NOT NULL,
    error            TEXT,
    model            TEXT,
    session_id       TEXT               -- browser session that asked (NULL from the CLI)
)
"""

SELECT_COLUMNS = "asked_at, question, sql, status, latency_seconds, row_count, error, model"


@dataclass(frozen=True)
class HistoryEntry:
    """One logged question."""

    asked_at: str
    question: str
    sql: str | None
    status: str
    latency_seconds: float
    row_count: int
    error: str | None
    model: str | None


class QueryHistory:
    """Reads and writes the history table in its own SQLite file."""

    def __init__(self, path: Path) -> None:
        """Open (and create if needed) the history file at `path`."""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._execute(CREATE_HISTORY_TABLE, ())
        self._add_session_column_if_missing()

    def _add_session_column_if_missing(self) -> None:
        """Upgrade a history file created before the session_id column existed."""
        columns = [row[1] for row in self._execute("PRAGMA table_info(history)", ())]
        if "session_id" not in columns:
            self._execute("ALTER TABLE history ADD COLUMN session_id TEXT", ())

    def _execute(self, sql: str, params: tuple) -> list[tuple]:
        """Run one statement on a short-lived connection, commit, and return all rows.

        Opening a new connection per call keeps this safe when Streamlit runs code
        in different threads. The connection is always closed afterwards, because
        an open connection keeps the file locked on Windows.
        """
        connection = sqlite3.connect(self.path)
        try:
            rows = connection.execute(sql, params).fetchall()
            connection.commit()
            return rows
        finally:
            connection.close()

    def log(
        self, result: PipelineResult, model: str | None = None, session_id: str | None = None
    ) -> None:
        """Save one pipeline result, optionally tagged with the browser session that asked."""
        asked_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._execute(
            "INSERT INTO history (asked_at, question, sql, status, latency_seconds, "
            "row_count, error, model, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asked_at,
                result.question,
                result.sql,
                result.status,
                round(result.latency_seconds, 3),
                len(result.rows),
                result.error,
                model,
                session_id,
            ),
        )

    def recent(self, limit: int = 20, session_id: str | None = None) -> list[HistoryEntry]:
        """Return the most recent entries, newest first.

        If session_id is given, only that session's entries are returned, so on a
        public app each visitor sees only their own questions.
        """
        if session_id is None:
            rows = self._execute(
                f"SELECT {SELECT_COLUMNS} FROM history ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = self._execute(
                f"SELECT {SELECT_COLUMNS} FROM history WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        return [HistoryEntry(*row) for row in rows]
