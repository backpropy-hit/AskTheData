"""Tests for askdata.evaluation.metrics with small hand-made examples."""

import pytest

from askdata.evaluation.metrics import (
    gold_has_order_by,
    normalise_rows,
    results_match,
    score,
)

# ---------- normalising values ----------


def test_numbers_are_rounded_to_two_decimals() -> None:
    """12.3456 -> 12.35; ints become floats so 5 equals 5.0; text and NULL unchanged."""
    assert normalise_rows([(12.3456, 5, "Kenya", None)]) == [(12.35, 5.0, "Kenya", None)]


def test_int_and_float_compare_equal() -> None:
    """SUM() can give 5 while AVG()-style maths gives 5.0; both are the same answer."""
    assert results_match([(5,)], [(5.0,)], ordered=False)


def test_tiny_float_differences_are_ignored() -> None:
    """Values equal after rounding to 2 decimals match."""
    assert results_match([(1.234,)], [(1.2349,)], ordered=False)


def test_real_differences_are_not_ignored() -> None:
    """A difference in the second decimal is a mismatch."""
    assert not results_match([(1.23,)], [(1.24,)], ordered=False)


# ---------- ORDER BY detection ----------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT name FROM countries ORDER BY name", True),
        ("SELECT name FROM countries", False),
        # ORDER BY only inside a subquery does not fix the final order.
        ("SELECT name FROM (SELECT name FROM countries ORDER BY name LIMIT 5)", False),
        ("WITH t AS (SELECT name FROM countries ORDER BY name) SELECT name FROM t", False),
        ("WITH t AS (SELECT 1 AS x) SELECT x FROM t ORDER BY x DESC", True),
    ],
)
def test_gold_has_order_by(sql: str, expected: bool) -> None:
    """Only an ORDER BY on the outermost query counts."""
    assert gold_has_order_by(sql) is expected


# ---------- comparing rows ----------


def test_unordered_comparison_ignores_row_order() -> None:
    """Without ORDER BY in the gold query, the same rows in any order match."""
    gold = [("Kenya", 10), ("Chad", 20)]
    predicted = [("Chad", 20), ("Kenya", 10)]
    assert results_match(gold, predicted, ordered=False)


def test_ordered_comparison_requires_same_order() -> None:
    """With ORDER BY in the gold query, a different order is a mismatch."""
    gold = [("Chad", 20), ("Kenya", 10)]
    predicted = [("Kenya", 10), ("Chad", 20)]
    assert not results_match(gold, predicted, ordered=True)
    assert results_match(gold, list(gold), ordered=True)


def test_column_names_are_not_part_of_the_comparison() -> None:
    """Only values are compared, so aliases like 'total' vs 'SUM(refugees)' do not matter."""
    # results_match never sees column names; this documents that by design.
    assert results_match([(100,)], [(100,)], ordered=False)


def test_column_order_matters() -> None:
    """(name, value) and (value, name) are different tuples."""
    assert not results_match([("Kenya", 10)], [(10, "Kenya")], ordered=False)


def test_extra_column_is_a_mismatch() -> None:
    """Returning an extra column (e.g. the iso3 code) makes the rows differ."""
    assert not results_match([("Kenya", 10)], [("KEN", "Kenya", 10)], ordered=False)


def test_missing_row_is_a_mismatch() -> None:
    """One row fewer than gold is wrong."""
    assert not results_match([("A", 1), ("B", 2)], [("A", 1)], ordered=False)


def test_duplicates_are_ignored_in_set_comparison() -> None:
    """Set comparison (as the proposal specifies) ignores repeated rows."""
    assert results_match([("A", 1)], [("A", 1), ("A", 1)], ordered=False)


def test_empty_results_match() -> None:
    """Two empty results are equal (e.g. 'which countries had zero ...' with no rows)."""
    assert results_match([], [], ordered=False)


# ---------- scoring a whole answer ----------


def test_unanswerable_refusal_passes() -> None:
    """A refusal on an unanswerable question is a pass."""
    verdict = score(False, "refused", None, None, [])
    assert verdict.passed


@pytest.mark.parametrize("status", ["ok", "sql_error", "rejected", "llm_error"])
def test_unanswerable_without_refusal_fails(status: str) -> None:
    """Running any SQL (even successfully) on an unanswerable question is a fail."""
    verdict = score(False, status, None, None, [(1,)])
    assert not verdict.passed
    assert status in verdict.reason


def test_answerable_refusal_fails() -> None:
    """Refusing a question the data can answer is a fail."""
    verdict = score(True, "refused", "SELECT 1", [(1,)], [])
    assert not verdict.passed
    assert "refused" in verdict.reason


@pytest.mark.parametrize("status", ["sql_error", "rejected", "llm_error"])
def test_answerable_failed_query_fails(status: str) -> None:
    """A query that never ran cannot be correct."""
    assert not score(True, status, "SELECT 1", [(1,)], []).passed


def test_answerable_matching_rows_pass() -> None:
    """Matching rows (after rounding) pass."""
    verdict = score(True, "ok", "SELECT x FROM t", [(1.004,)], [(1,)])
    assert verdict.passed
    assert verdict.reason == "rows match"


def test_answerable_order_checked_when_gold_orders() -> None:
    """When the gold query has ORDER BY, a different order fails and says why."""
    gold_sql = "SELECT name, n FROM t ORDER BY n DESC"
    verdict = score(True, "ok", gold_sql, [("A", 2), ("B", 1)], [("B", 1), ("A", 2)])
    assert not verdict.passed
    assert "order" in verdict.reason


def test_answerable_without_gold_raises() -> None:
    """Missing gold data for an answerable question is a bug in the question file."""
    with pytest.raises(ValueError):
        score(True, "ok", None, None, [(1,)])
