"""Tests for askdata.display: chart choice and user messages."""

import pytest

from askdata.display import ChartSpec, choose_chart, status_message
from askdata.nl2sql.pipeline import PipelineResult

# ---------- choose_chart ----------


def test_year_and_number_gives_line_chart() -> None:
    """A trend over years is drawn as a line."""
    chart = choose_chart(["year", "total"], [(2020, 5), (2021, 7)])
    assert chart == ChartSpec(kind="line", x="year", y="total")


def test_text_and_number_gives_bar_chart() -> None:
    """Values per country are drawn as bars."""
    chart = choose_chart(["name", "refugees"], [("Chad", 10), ("Kenya", 20)])
    assert chart == ChartSpec(kind="bar", x="name", y="refugees")


def test_column_order_does_not_matter() -> None:
    """A number followed by a label still gives a chart with the label on x."""
    chart = choose_chart(["refugees", "name"], [(10, "Chad"), (20, "Kenya")])
    assert chart == ChartSpec(kind="bar", x="name", y="refugees")


def test_year_column_name_is_case_insensitive() -> None:
    """'Year' counts as a time column too."""
    chart = choose_chart(["Year", "idps"], [(2020, 1.5), (2021, 2.5)])
    assert chart is not None and chart.kind == "line"


def test_float_values_are_numeric() -> None:
    """Ratios such as refugees per 1,000 people can be charted."""
    chart = choose_chart(["region", "per_1000"], [("A", 1.25), ("B", 3.5)])
    assert chart == ChartSpec(kind="bar", x="region", y="per_1000")


def test_nulls_are_ignored_when_checking_types() -> None:
    """A NULL value does not stop a chart."""
    chart = choose_chart(["name", "total"], [("A", None), ("B", 3), ("C", 4)])
    assert chart is not None


@pytest.mark.parametrize(
    ("columns", "rows"),
    [
        (["total"], [(1,), (2,)]),  # one column
        (["name", "a", "b"], [("x", 1, 2), ("y", 3, 4)]),  # three columns
        (["name", "total"], [("Chad", 10)]),  # a single row
        (["name", "total"], []),  # no rows
        (["a", "b"], [(1, 2), (3, 4)]),  # two numbers, neither is a year
        (["name", "region"], [("Chad", "X"), ("Kenya", "Y")]),  # two text columns
        (["name", "total"], [(None, None), (None, None)]),  # all NULL
    ],
)
def test_no_chart_when_it_would_not_help(columns: list[str], rows: list[tuple]) -> None:
    """Results that are not 'one label + one number' get no chart."""
    assert choose_chart(columns, rows) is None


# ---------- status_message ----------


def test_ok_message_counts_rows() -> None:
    """Success says how many rows came back, with correct singular/plural."""
    one = status_message(PipelineResult(question="q", status="ok", rows=[(1,)], attempts=1))
    two = status_message(PipelineResult(question="q", status="ok", rows=[(1,), (2,)], attempts=1))
    assert one.level == "success" and "1 row." in one.text
    assert "2 rows." in two.text


def test_ok_after_retry_mentions_correction() -> None:
    """If the retry was needed, the message says so."""
    message = status_message(PipelineResult(question="q", status="ok", attempts=2))
    assert "after one correction" in message.text


@pytest.mark.parametrize(
    ("status", "level", "phrase"),
    [
        ("refused", "info", "cannot be answered"),
        ("rejected", "error", "safety check"),
        ("sql_error", "error", "failed when it ran"),
        ("llm_error", "error", "could not be reached"),
        ("something_new", "error", "Unexpected status"),
    ],
)
def test_messages_for_each_status(status: str, level: str, phrase: str) -> None:
    """Each outcome gets a clear message; errors include the detail."""
    result = PipelineResult(question="q", status=status, error="DETAIL")
    message = status_message(result)
    assert message.level == level
    assert phrase in message.text
    if level == "error" and status != "something_new":
        assert "DETAIL" in message.text
