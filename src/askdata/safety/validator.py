"""Check generated SQL before it is run.

This is the first safety layer; the read-only connection is the second.
A query passes only if it is:
  * exactly one statement,
  * a SELECT (WITH ... SELECT and UNION/INTERSECT/EXCEPT of SELECTs are fine),
  * free of anything that changes data or the database (INSERT, DROP, PRAGMA, ATTACH, ...),
  * reading only from the known tables (plus its own WITH names).

If the query has no LIMIT, one is added so a result can never be huge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# sqlglot logs a warning when it cannot fully parse a statement (e.g. VACUUM).
# We reject such statements anyway, so the warning is only noise.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Statement types that must never appear anywhere in the query.
# getattr(..., None) keeps this working if a sqlglot version lacks one of them.
FORBIDDEN_NODE_NAMES = [
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Drop",
    "Create",
    "Alter",
    "TruncateTable",
    "Pragma",
    "Attach",
    "Detach",
    "Command",  # anything sqlglot does not understand, e.g. VACUUM, REPLACE INTO
]
FORBIDDEN_NODE_TYPES: tuple[type, ...] = tuple(
    node_type
    for node_type in (getattr(exp, name, None) for name in FORBIDDEN_NODE_NAMES)
    if node_type is not None
)

# SQL functions that are not allowed even inside a SELECT.
FORBIDDEN_FUNCTIONS = {"load_extension"}


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one SQL string.

    If ok is True, `sql` holds the query to run (with a LIMIT added if needed).
    If ok is False, `error` explains why the query was rejected.
    """

    ok: bool
    sql: str | None
    error: str | None


def _reject(message: str) -> ValidationResult:
    """Build a failed ValidationResult."""
    return ValidationResult(ok=False, sql=None, error=message)


def _parse_single_statement(sql: str) -> exp.Expression | ValidationResult:
    """Parse sql and return its one statement, or a rejection if that is not possible."""
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as error:
        # error.errors holds plain descriptions; str(error) contains terminal colour codes.
        details = error.errors[0]["description"] if error.errors else "unknown problem"
        return _reject(f"The SQL could not be parsed: {details}.")

    # A trailing semicolon can produce an empty (None) entry; ignore those.
    statements = [statement for statement in statements if statement is not None]
    if len(statements) == 0:
        return _reject("The SQL is empty.")
    if len(statements) > 1:
        return _reject(f"Only one statement is allowed, but {len(statements)} were found.")
    return statements[0]


def _cte_names(statement: exp.Expression) -> set[str]:
    """Return the lower-case names defined in WITH clauses (they are not real tables)."""
    return {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}


def _check_tables(statement: exp.Expression, allowed_tables: set[str]) -> str | None:
    """Return an error message if the query reads from anything but the allowed tables."""
    cte_names = _cte_names(statement)
    for table in statement.find_all(exp.Table):
        # Table-valued functions such as pragma_table_info(...) or json_each(...)
        # appear as a Table whose `this` is a function call instead of a name.
        if not isinstance(table.this, exp.Identifier):
            return f"Table functions are not allowed: {table.sql(dialect='sqlite')}."
        # A prefix such as main.countries or other_db.countries points at a database.
        if table.db or table.catalog:
            return f"Database prefixes are not allowed: {table.sql(dialect='sqlite')}."
        name = table.name.lower()
        if name not in allowed_tables and name not in cte_names:
            allowed = ", ".join(sorted(allowed_tables))
            return f"Unknown table '{table.name}'. Allowed tables: {allowed}."
    return None


def _check_functions(statement: exp.Expression) -> str | None:
    """Return an error message if a forbidden SQL function is used."""
    for function in statement.find_all(exp.Func):
        # Unknown functions are exp.Anonymous with the name in `.name`;
        # known ones have a fixed SQL name.
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.lower() in FORBIDDEN_FUNCTIONS:
            return f"The function {name} is not allowed."
    return None


def validate_sql(sql: str, allowed_tables: set[str], row_limit: int) -> ValidationResult:
    """Check that sql is one safe SELECT over allowed tables; add a LIMIT if missing.

    Args:
        sql: The SQL text produced by the model.
        allowed_tables: Table names the query may read (compared case-insensitively).
        row_limit: LIMIT added when the query has none.
    """
    parsed = _parse_single_statement(sql)
    if isinstance(parsed, ValidationResult):
        return parsed
    statement = parsed

    # The top-level statement must be a SELECT or a set operation (UNION etc.) of SELECTs.
    if not isinstance(statement, (exp.Select, exp.SetOperation)):
        kind = statement.key.upper()
        return _reject(f"Only SELECT queries are allowed, but this is a {kind} statement.")

    # Also look inside the query: a subquery or WITH part must not hide a write.
    for node in statement.walk():
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            return _reject(f"{node.key.upper()} is not allowed; only SELECT queries can run.")

    lower_allowed = {table.lower() for table in allowed_tables}
    table_error = _check_tables(statement, lower_allowed)
    if table_error is not None:
        return _reject(table_error)

    function_error = _check_functions(statement)
    if function_error is not None:
        return _reject(function_error)

    if statement.args.get("limit") is None:
        # Rebuild the SQL from the parsed tree with a LIMIT on the outermost query.
        final_sql = statement.limit(row_limit).sql(dialect="sqlite")
    else:
        # Keep the model's own text unchanged when nothing needs adding.
        final_sql = sql.strip().rstrip(";").strip()

    return ValidationResult(ok=True, sql=final_sql, error=None)
