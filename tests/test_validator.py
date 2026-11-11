"""Tests for askdata.safety.validator."""

import pytest

from askdata.safety.validator import validate_sql

TABLES = {"countries", "population", "displacement"}
LIMIT = 100


def check(sql: str):
    """Validate sql with the project's tables and a LIMIT of 100."""
    return validate_sql(sql, TABLES, LIMIT)


# ---------- queries that must pass ----------


def test_simple_select_passes_and_gets_limit() -> None:
    """A plain SELECT passes and gets LIMIT 100 added."""
    result = check("SELECT name FROM countries WHERE region = 'South Asia'")
    assert result.ok
    assert result.error is None
    assert result.sql.endswith("LIMIT 100")


def test_existing_limit_is_kept() -> None:
    """A query with its own LIMIT is returned unchanged (minus the semicolon)."""
    result = check("SELECT name FROM countries LIMIT 5;")
    assert result.ok
    assert result.sql == "SELECT name FROM countries LIMIT 5"


@pytest.mark.parametrize(
    "sql",
    [
        # join of the same table twice (origin and asylum)
        "SELECT o.name, a.name FROM displacement d "
        "JOIN countries o ON o.iso3 = d.origin_iso3 "
        "JOIN countries a ON a.iso3 = d.asylum_iso3",
        # WITH clause: 'totals' is not a real table, but it is defined in the query
        "WITH totals AS (SELECT asylum_iso3, SUM(refugees) AS r FROM displacement "
        "GROUP BY asylum_iso3) SELECT * FROM totals ORDER BY r DESC",
        # subquery
        "SELECT name FROM countries WHERE iso3 IN (SELECT iso3 FROM population)",
        # set operation
        "SELECT origin_iso3 FROM displacement UNION SELECT asylum_iso3 FROM displacement",
        # table names are not case-sensitive in SQLite
        "SELECT * FROM Countries",
    ],
)
def test_valid_selects_pass(sql: str) -> None:
    """Joins, WITH, subqueries, UNION and mixed-case names are all allowed."""
    result = check(sql)
    assert result.ok, result.error


def test_union_gets_limit_on_whole_query() -> None:
    """The LIMIT is added once, to the outermost query."""
    result = check("SELECT iso3 FROM countries UNION SELECT iso3 FROM population")
    assert result.ok
    assert result.sql.endswith("LIMIT 100")
    assert result.sql.count("LIMIT") == 1


# ---------- queries that must be rejected ----------


@pytest.mark.parametrize(
    ("sql", "expected_error"),
    [
        ("DROP TABLE countries", "Only SELECT"),
        ("INSERT INTO countries (iso3, name) VALUES ('ZZZ', 'X')", "Only SELECT"),
        ("UPDATE countries SET name = 'X'", "Only SELECT"),
        ("DELETE FROM displacement", "Only SELECT"),
        ("CREATE TABLE t (x INTEGER)", "Only SELECT"),
        ("ALTER TABLE countries ADD COLUMN x INTEGER", "Only SELECT"),
        ("PRAGMA table_info(countries)", "Only SELECT"),
        ("ATTACH DATABASE 'other.db' AS other", "Only SELECT"),
        ("DETACH DATABASE other", "Only SELECT"),
        ("VACUUM", "Only SELECT"),
        ("REPLACE INTO countries VALUES ('ZZZ', 'X', NULL, NULL)", "Only SELECT"),
    ],
)
def test_non_select_statements_are_rejected(sql: str, expected_error: str) -> None:
    """DDL, DML, PRAGMA, ATTACH and unparseable commands are blocked."""
    result = check(sql)
    assert not result.ok
    assert result.sql is None
    assert expected_error in result.error


def test_multiple_statements_are_rejected() -> None:
    """A second statement hidden after a semicolon is blocked."""
    result = check("SELECT * FROM countries; DROP TABLE countries")
    assert not result.ok
    assert "Only one statement" in result.error


def test_two_selects_are_also_rejected() -> None:
    """Even two harmless SELECTs count as more than one statement."""
    assert not check("SELECT 1; SELECT 2").ok


@pytest.mark.parametrize(
    ("sql", "expected_error"),
    [
        ("SELECT * FROM refugees_2030", "Unknown table 'refugees_2030'"),
        ("SELECT * FROM sqlite_master", "Unknown table 'sqlite_master'"),
        ("SELECT name FROM countries WHERE iso3 IN (SELECT x FROM secret)", "Unknown table"),
        ("SELECT * FROM pragma_table_info('countries')", "Table functions"),
        ("SELECT * FROM json_each('[1, 2]')", "Table functions"),
        ("SELECT * FROM main.countries", "Database prefixes"),
        ("SELECT * FROM other.countries", "Database prefixes"),
    ],
)
def test_unknown_or_indirect_tables_are_rejected(sql: str, expected_error: str) -> None:
    """Only the three project tables may be read, by plain name."""
    result = check(sql)
    assert not result.ok
    assert expected_error in result.error


def test_load_extension_is_rejected() -> None:
    """load_extension could run native code, so it is blocked."""
    result = check("SELECT load_extension('evil.dll')")
    assert not result.ok
    assert "load_extension" in result.error


@pytest.mark.parametrize("sql", ["", "   ", ";"])
def test_empty_sql_is_rejected(sql: str) -> None:
    """Empty input is rejected with a clear message."""
    result = check(sql)
    assert not result.ok
    assert "empty" in result.error


def test_unparseable_sql_is_rejected_with_plain_message() -> None:
    """Broken SQL gives a readable error without terminal colour codes."""
    result = check("SELEC name FROM countries")
    assert not result.ok
    assert "could not be parsed" in result.error
    assert "\x1b[" not in result.error


def test_refusal_text_is_not_valid_sql() -> None:
    """If a refusal slipped through to the validator, it is rejected, not run."""
    assert not check("CANNOT ANSWER: cannot answer from this data").ok
