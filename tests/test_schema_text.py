"""Tests for askdata.db.schema_text, using the fake dataset from conftest.py."""

import sqlite3
from pathlib import Path

import pytest

from askdata.db import schema_text
from askdata.db.connection import connect_read_only
from askdata.db.schema_text import COLUMN_DESCRIPTIONS, TABLE_ORDER, build_schema_text


@pytest.fixture
def connection(db_path: Path) -> sqlite3.Connection:
    """A read-only connection to the small test database."""
    return connect_read_only(db_path)


def test_contains_create_statement_for_every_table(connection: sqlite3.Connection) -> None:
    """Each table appears with its real CREATE TABLE statement."""
    text = build_schema_text(connection)
    for table in TABLE_ORDER:
        assert f"CREATE TABLE {table}" in text


def test_every_database_column_has_a_description(connection: sqlite3.Connection) -> None:
    """Descriptions cover exactly the columns that exist (no missing, no stale)."""
    for table in TABLE_ORDER:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {row[1] for row in rows}
        assert columns == set(COLUMN_DESCRIPTIONS[table]), table


def test_every_description_line_is_in_text(connection: sqlite3.Connection) -> None:
    """Each column gets a one-line description in the output."""
    text = build_schema_text(connection)
    for table, descriptions in COLUMN_DESCRIPTIONS.items():
        for column, description in descriptions.items():
            assert f"--   {column}: {description}" in text, f"{table}.{column}"


def test_categorical_values_come_from_the_data(connection: sqlite3.Connection) -> None:
    """Region and income values are the real ones from the database, NULLs excluded."""
    text = build_schema_text(connection)
    assert (
        "-- All values of region: 'Europe & Central Asia', "
        "'Middle East & North Africa', 'Sub-Saharan Africa'"
    ) in text
    assert "'None'" not in text


def test_name_examples_and_year_range(connection: sqlite3.Connection) -> None:
    """Tricky country names and the covered years are shown."""
    text = build_schema_text(connection)
    assert "'TUR' = 'Türkiye'" in text
    assert "-- Years in this table: 2024 to 2025." in text


def test_undocumented_column_raises(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a column has no description, building the text fails instead of hiding it."""
    reduced = {table: dict(columns) for table, columns in COLUMN_DESCRIPTIONS.items()}
    del reduced["population"]["total"]
    monkeypatch.setattr(schema_text, "COLUMN_DESCRIPTIONS", reduced)
    with pytest.raises(ValueError, match="total"):
        build_schema_text(connection)
