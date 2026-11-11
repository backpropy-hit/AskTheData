"""Tests for askdata.history."""

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


def test_history_persists_across_instances(tmp_path: Path) -> None:
    """The log is a file: a new QueryHistory object sees earlier entries."""
    path = tmp_path / "sub" / "history.db"
    QueryHistory(path).log(PipelineResult(question="q", status="sql_error", error="boom"))
    entries = QueryHistory(path).recent()
    assert entries[0].error == "boom"
