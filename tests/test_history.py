"""Tests for askdata.history."""

import sqlite3
from pathlib import Path

from askdata.history import QueryHistory
from askdata.nl2sql.pipeline import PipelineResult


def test_log_and_read_back(tmp_path: Path) -> None:
    """A logged result can be read back with all its fields."""
    history = QueryHistory(tmp_path / "history.db")
    result = PipelineResult(
        question="How many?",
        status="ok",
        sql="SELECT 1 LIMIT 100",
        columns=["x"],
        rows=[(1,), (2,)],
        latency_seconds=1.23456,
    )
    history.log(result, model="test-model")

    entries = history.recent()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.question == "How many?"
    assert entry.sql == "SELECT 1 LIMIT 100"
    assert entry.status == "ok"
    assert entry.row_count == 2
    assert entry.latency_seconds == 1.235
    assert entry.model == "test-model"
    assert entry.error is None


def test_recent_is_newest_first_and_limited(tmp_path: Path) -> None:
    """recent() returns the newest entries first, up to the limit."""
    history = QueryHistory(tmp_path / "history.db")
    for number in range(5):
        history.log(PipelineResult(question=f"q{number}", status="refused"))

    entries = history.recent(limit=3)
    assert [entry.question for entry in entries] == ["q4", "q3", "q2"]


def test_recent_can_be_limited_to_one_session(tmp_path: Path) -> None:
    """Each session sees only its own questions; without a session, all are returned."""
    history = QueryHistory(tmp_path / "history.db")
    history.log(PipelineResult(question="mine", status="ok"), session_id="A")
    history.log(PipelineResult(question="theirs", status="ok"), session_id="B")
    history.log(PipelineResult(question="from cli", status="ok"))

    assert [entry.question for entry in history.recent(session_id="A")] == ["mine"]
    assert len(history.recent()) == 3


def test_old_history_file_is_upgraded(tmp_path: Path) -> None:
    """A file created before session_id existed gets the column added, keeping old rows."""
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, asked_at TEXT NOT NULL, "
        "question TEXT NOT NULL, sql TEXT, status TEXT NOT NULL, latency_seconds REAL NOT NULL, "
        "row_count INTEGER NOT NULL, error TEXT, model TEXT)"
    )
    connection.execute(
        "INSERT INTO history (asked_at, question, status, latency_seconds, row_count) "
        "VALUES ('2026-01-01', 'old question', 'ok', 1.0, 1)"
    )
    connection.commit()
    connection.close()

    history = QueryHistory(path)
    history.log(PipelineResult(question="new question", status="ok"), session_id="S")
    assert [entry.question for entry in history.recent()] == ["new question", "old question"]
    assert [entry.question for entry in history.recent(session_id="S")] == ["new question"]


def test_history_persists_across_instances(tmp_path: Path) -> None:
    """The log is a file: a new QueryHistory object sees earlier entries."""
    path = tmp_path / "sub" / "history.db"
    QueryHistory(path).log(PipelineResult(question="q", status="sql_error", error="boom"))
    entries = QueryHistory(path).recent()
    assert entries[0].error == "boom"
