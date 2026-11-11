"""Build the schema description that is put into the LLM prompt.

For each table the text contains:
  * the real CREATE TABLE statement, read from the database itself,
  * a one-line description of every column,
  * real example values for categorical columns (models guess these wrong otherwise),
  * the range of years covered.

Everything except the descriptions is read from the database, so the text
always matches the data the query will run on.
"""

from __future__ import annotations

import sqlite3

TABLE_ORDER: list[str] = ["countries", "population", "displacement"]

COLUMN_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "countries": {
        "iso3": "Three-letter country code (ISO 3166 alpha-3), e.g. 'KEN'. "
        "Also UNHCR codes 'XXA' = Stateless, 'UNK' = Unknown origin, 'TIB' = Tibetan.",
        "name": "Country name in World Bank spelling (see examples below).",
        "region": "World Bank region; NULL for the UNHCR-only codes "
        "(they are not World Bank countries and have no population rows).",
        "income_group": "World Bank income group; NULL for the UNHCR-only codes.",
    },
    "population": {
        "iso3": "Country code; joins to countries.iso3.",
        "year": "Calendar year.",
        "total": "Total mid-year population of the country (people).",
    },
    "displacement": {
        "year": "Year of the figures (end-of-year counts).",
        "origin_iso3": "Country the people come from; joins to countries.iso3.",
        "asylum_iso3": "Country where the people are now (host or asylum country); "
        "joins to countries.iso3.",
        "refugees": "Refugees under UNHCR's mandate from origin_iso3 living in asylum_iso3.",
        "asylum_seekers": "People whose asylum claim in asylum_iso3 is not yet decided.",
        "returned_refugees": "Refugees who went back from asylum_iso3 to origin_iso3 "
        "during the year.",
        "idps": "Internally displaced people (displaced inside their own country, "
        "so origin_iso3 = asylum_iso3 on these rows).",
        "returned_idps": "Internally displaced people who returned home during the year "
        "(origin_iso3 = asylum_iso3).",
        "stateless": "Stateless persons living in asylum_iso3 "
        "(people not recognised as citizens by any country).",
        "others_of_concern": "Other people of concern to UNHCR not in the categories above.",
        "host_community": "Members of host communities supported by UNHCR.",
    },
}

# Columns whose distinct values are listed in full in the prompt.
CATEGORICAL_COLUMNS: dict[str, list[str]] = {
    "countries": ["region", "income_group"],
}

# Countries whose World Bank names are easy to guess wrong.
NAME_EXAMPLE_CODES: list[str] = ["COD", "IRN", "PSE", "SYR", "TUR", "VEN"]

MAX_CATEGORICAL_VALUES = 15


def get_create_statement(connection: sqlite3.Connection, table: str) -> str:
    """Return the CREATE TABLE statement stored by SQLite for one table."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Table '{table}' does not exist in the database.")
    return row[0]


def get_column_names(connection: sqlite3.Connection, table: str) -> list[str]:
    """Return the column names of a table, in table order."""
    # Table names come from the constants above, never from user input.
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    # Each row is (cid, name, type, notnull, default, pk); index 1 is the name.
    return [row[1] for row in rows]


def get_distinct_values(
    connection: sqlite3.Connection, table: str, column: str, limit: int
) -> list[str]:
    """Return up to `limit` distinct non-NULL values of a column, sorted."""
    rows = connection.execute(
        f'SELECT DISTINCT "{column}" FROM "{table}" '
        f'WHERE "{column}" IS NOT NULL ORDER BY "{column}" LIMIT ?',
        (limit,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def get_year_range(connection: sqlite3.Connection, table: str) -> tuple[int, int] | None:
    """Return (first year, last year) for a table with a `year` column, else None."""
    if "year" not in get_column_names(connection, table):
        return None
    first, last = connection.execute(f'SELECT MIN(year), MAX(year) FROM "{table}"').fetchone()
    if first is None:
        return None
    return int(first), int(last)


def get_name_examples(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (iso3, name) pairs for the countries in NAME_EXAMPLE_CODES that exist."""
    placeholders = ", ".join("?" for _ in NAME_EXAMPLE_CODES)
    rows = connection.execute(
        f"SELECT iso3, name FROM countries WHERE iso3 IN ({placeholders}) ORDER BY iso3",
        NAME_EXAMPLE_CODES,
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def describe_table(connection: sqlite3.Connection, table: str) -> str:
    """Return the prompt text for one table: CREATE statement, descriptions, examples.

    Raises:
        ValueError: If a column in the database has no description, so the
            prompt never silently leaves a column unexplained.
    """
    descriptions = COLUMN_DESCRIPTIONS.get(table, {})
    columns = get_column_names(connection, table)
    undocumented = [column for column in columns if column not in descriptions]
    if undocumented:
        raise ValueError(f"No description for columns in '{table}': {undocumented}")

    lines = [get_create_statement(connection, table) + ";", "-- Columns:"]
    for column in columns:
        lines.append(f"--   {column}: {descriptions[column]}")

    year_range = get_year_range(connection, table)
    if year_range is not None:
        lines.append(f"-- Years in this table: {year_range[0]} to {year_range[1]}.")

    for column in CATEGORICAL_COLUMNS.get(table, []):
        values = get_distinct_values(connection, table, column, MAX_CATEGORICAL_VALUES)
        quoted = ", ".join(f"'{value}'" for value in values)
        lines.append(f"-- All values of {column}: {quoted}")

    if table == "countries":
        examples = ", ".join(f"'{iso3}' = '{name}'" for iso3, name in get_name_examples(connection))
        lines.append(f"-- Example names: {examples}")

    return "\n".join(lines)


def build_schema_text(connection: sqlite3.Connection) -> str:
    """Return the full schema description for all tables, ready for the prompt."""
    return "\n\n".join(describe_table(connection, table) for table in TABLE_ORDER)
