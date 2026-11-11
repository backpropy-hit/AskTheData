"""Execution accuracy: is the generated query's result the same as the gold query's?

Rules (from the project proposal):
  * Compare the rows only; column names are ignored.
  * If the gold SQL has an ORDER BY on the outermost query, rows must be in the
    same order. Otherwise rows are compared as sets of tuples.
  * Numbers are rounded to 2 decimals before comparing, so 12.3456 equals 12.35
    and 5 equals 5.0.
  * For an unanswerable question, a refusal is a pass and anything else a fail.

Known limits of this metric (also listed in the README):
  * Column order matters: (name, total) does not match (total, name).
  * An extra column (e.g. returning the iso3 code as well) makes the rows differ.
  * Because rows are compared as sets, duplicate rows are ignored.
  * A wrong query can pass if it happens to return the same rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot

DECIMALS = 2


@dataclass(frozen=True)
class Verdict:
    """Whether one generated answer counts as correct, and a short reason."""

    passed: bool
    reason: str


def normalise_value(value: Any) -> Any:
    """Make one cell comparable: numbers become floats rounded to DECIMALS places."""
    # bool is a subclass of int; SQLite has no booleans, but keep True/False as they are.
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), DECIMALS)
    return value


def normalise_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Apply normalise_value to every cell, keeping row and column order."""
    return [tuple(normalise_value(value) for value in row) for row in rows]


def gold_has_order_by(gold_sql: str) -> bool:
    """Return True if the outermost query of gold_sql has an ORDER BY.

    An ORDER BY inside a subquery or WITH clause does not fix the order of the
    final result, so only the top-level query is checked.
    """
    statement = sqlglot.parse_one(gold_sql, read="sqlite")
    return statement.args.get("order") is not None


def results_match(
    gold_rows: list[tuple[Any, ...]],
    predicted_rows: list[tuple[Any, ...]],
    ordered: bool,
) -> bool:
    """Compare two results after normalising numbers.

    Args:
        gold_rows: Rows returned by the gold query.
        predicted_rows: Rows returned by the generated query.
        ordered: If True, rows must appear in the same order.
    """
    gold = normalise_rows(gold_rows)
    predicted = normalise_rows(predicted_rows)
    if ordered:
        return gold == predicted
    return set(gold) == set(predicted)


def score(
    answerable: bool,
    status: str,
    gold_sql: str | None,
    gold_rows: list[tuple[Any, ...]] | None,
    predicted_rows: list[tuple[Any, ...]],
) -> Verdict:
    """Decide whether one pipeline answer is correct.

    Args:
        answerable: Whether the question can be answered from the data.
        status: Pipeline status ("ok", "refused", "rejected", "sql_error", "llm_error").
        gold_sql: The gold query (None for unanswerable questions).
        gold_rows: Rows from running the gold query (None for unanswerable questions).
        predicted_rows: Rows from the generated query (empty if it did not run).
    """
    if not answerable:
        if status == "refused":
            return Verdict(True, "correctly refused")
        return Verdict(False, f"should have refused, but status was '{status}'")

    if status == "refused":
        return Verdict(False, "refused an answerable question")
    if status != "ok":
        return Verdict(False, f"query did not run (status '{status}')")
    if gold_sql is None or gold_rows is None:
        raise ValueError("An answerable question needs gold SQL and gold rows.")

    ordered = gold_has_order_by(gold_sql)
    if results_match(gold_rows, predicted_rows, ordered):
        return Verdict(True, "rows match" + (" in order" if ordered else ""))
    return Verdict(False, "rows differ" + (" (order checked)" if ordered else ""))
