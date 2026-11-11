"""Display decisions for the app, kept here so they can be tested without Streamlit.

* choose_chart: should the result be drawn, and as a bar or a line chart?
* status_message: what to tell the user for each pipeline outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from askdata.nl2sql.pipeline import (
    STATUS_LLM_ERROR,
    STATUS_OK,
    STATUS_REFUSED,
    STATUS_REJECTED,
    STATUS_SQL_ERROR,
    PipelineResult,
)

# Column names treated as time (drawn as a line chart).
TIME_COLUMN_NAMES = {"year"}


@dataclass(frozen=True)
class ChartSpec:
    """Which chart to draw and which column goes on which axis."""

    kind: str  # "bar" or "line"
    x: str  # category or time column
    y: str  # numeric column


@dataclass(frozen=True)
class Message:
    """A message for the user and how to show it ("success", "info" or "error")."""

    level: str
    text: str


def _is_number(value: Any) -> bool:
    """True for ints and floats (bool is excluded even though it is an int subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _column_values(rows: list[tuple[Any, ...]], index: int) -> list[Any]:
    """Return the non-NULL values of one column."""
    return [row[index] for row in rows if row[index] is not None]


def choose_chart(columns: list[str], rows: list[tuple[Any, ...]]) -> ChartSpec | None:
    """Pick a simple chart for a result, or None if a chart would not help.

    A chart is drawn only for exactly two columns and at least two rows:
      * a time column ("year") + a numeric column  -> line chart
      * a text column + a numeric column           -> bar chart
    The two columns may come in either order.
    """
    if len(columns) != 2 or len(rows) < 2:
        return None

    first_values = _column_values(rows, 0)
    second_values = _column_values(rows, 1)
    if not first_values or not second_values:
        return None

    first_is_numeric = all(_is_number(value) for value in first_values)
    second_is_numeric = all(_is_number(value) for value in second_values)

    # Work out which column is the label (x) and which is the value (y).
    if second_is_numeric and (columns[0].lower() in TIME_COLUMN_NAMES or not first_is_numeric):
        x_index, y_index = 0, 1
    elif first_is_numeric and (columns[1].lower() in TIME_COLUMN_NAMES or not second_is_numeric):
        x_index, y_index = 1, 0
    else:
        return None  # two numbers that are not year + value, or two text columns

    x_name = columns[x_index]
    x_values = _column_values(rows, x_index)
    if x_name.lower() in TIME_COLUMN_NAMES and all(_is_number(value) for value in x_values):
        return ChartSpec(kind="line", x=x_name, y=columns[y_index])
    if all(isinstance(value, str) for value in x_values):
        return ChartSpec(kind="bar", x=x_name, y=columns[y_index])
    return None


def status_message(result: PipelineResult) -> Message:
    """Return the message to show for a pipeline result."""
    if result.status == STATUS_OK:
        row_word = "row" if len(result.rows) == 1 else "rows"
        retry_note = " after one correction" if result.attempts > 1 else ""
        return Message(
            "success", f"Query ran{retry_note} and returned {len(result.rows)} {row_word}."
        )
    if result.status == STATUS_REFUSED:
        return Message(
            "info",
            "This question cannot be answered from the available tables "
            "(for example forecasts, or data the tables do not contain).",
        )
    if result.status == STATUS_REJECTED:
        return Message(
            "error", f"The generated SQL was blocked by the safety check. {result.error}"
        )
    if result.status == STATUS_SQL_ERROR:
        return Message("error", f"The generated SQL failed when it ran. {result.error}")
    if result.status == STATUS_LLM_ERROR:
        return Message("error", f"The language model could not be reached. {result.error}")
    return Message("error", f"Unexpected status '{result.status}'.")
