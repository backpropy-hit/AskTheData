"""Tests for askdata.db.connection: read-only access and the query timeout."""

import sqlite3
from pathlib import Path

import pytest

from askdata.db.connection import QueryTimeoutError, connect_read_only, run_query

# A recursive query that never ends on its own; used to test the timeout.
ENDLESS_QUERY = """
WITH RECURSIVE counter(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM counter)
SELECT COUNT(*) FROM counter
"""


def test_select_returns_columns_and_rows(db_path: Path) -> None:
    """A normal SELECT works and returns column names and rows."""
    connection = connect_read_only(db_path)
    result = run_query(connection, "SELECT iso3, name FROM countries WHERE iso3 = 'KEN'", 5.0)
    assert result.columns == ["iso3", "name"]
    assert result.rows == [("KEN", "Kenya")]


def test_path_with_spaces_works(db_path: Path) -> None:
    """The conftest database lives in a folder with a space in its name."""
    assert " " in str(db_path)
    connection = connect_read_only(db_path)
    assert run_query(connection, "SELECT COUNT(*) FROM countries", 5.0).rows == [(6,)]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO countries (iso3, name) VALUES ('ZZZ', 'Nowhere')",
        "UPDATE countries SET name = 'X'",
        "DELETE FROM displacement",
        "DROP TABLE countries",
        "CREATE TABLE hack (x INTEGER)",
    ],
)
def test_writes_are_refused(db_path: Path, sql: str) -> None:
    """Even if a write reaches the connection, SQLite refuses it."""
    connection = connect_read_only(db_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
        run_query(connection, sql, 5.0)
    # The data is unchanged.
    assert run_query(connection, "SELECT COUNT(*) FROM countries", 5.0).rows == [(6,)]


def test_query_only_cannot_be_switched_off_silently(db_path: Path) -> None:
    """Turning off query_only does not help: the file itself is still read-only."""
    connection = connect_read_only(db_path)
    connection.execute("PRAGMA query_only = OFF")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        run_query(connection, "DELETE FROM countries", 5.0)


def test_slow_query_times_out(db_path: Path) -> None:
    """A query that runs past the time limit is stopped with QueryTimeoutError."""
    connection = connect_read_only(db_path)
    with pytest.raises(QueryTimeoutError):
        run_query(connection, ENDLESS_QUERY, 0.2)


def test_connection_usable_after_timeout(db_path: Path) -> None:
    """After a timeout the handler is removed and the next query runs normally."""
    connection = connect_read_only(db_path)
    with pytest.raises(QueryTimeoutError):
        run_query(connection, ENDLESS_QUERY, 0.2)
    assert run_query(connection, "SELECT COUNT(*) FROM countries", 5.0).rows == [(6,)]


def test_sql_errors_pass_through(db_path: Path) -> None:
    """Ordinary SQL errors are not turned into timeouts."""
    connection = connect_read_only(db_path)
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        run_query(connection, "SELECT not_a_column FROM countries", 5.0)


def test_missing_database_raises(tmp_path: Path) -> None:
    """A missing file gives a clear error instead of creating an empty database."""
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError, match="askdata.db.loader"):
        connect_read_only(missing)
    assert not missing.exists()
