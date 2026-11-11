"""Tests for askdata.nl2sql.generate."""

import pytest

from askdata.llm.prompts import REFUSAL_REPLY
from askdata.nl2sql.generate import clean_model_output, generate_sql, is_refusal
from fakes import FakeLLM


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SELECT 1;", "SELECT 1;"),
        ("  SELECT 1;\n", "SELECT 1;"),
        ("```sql\nSELECT 1;\n```", "SELECT 1;"),
        ("```\nSELECT 1;\n```", "SELECT 1;"),
        ("Here is the query:\n```sqlite\nSELECT 1;\n```\nHope it helps.", "SELECT 1;"),
        ("SQL: SELECT 1;", "SELECT 1;"),
    ],
)
def test_clean_model_output(raw: str, expected: str) -> None:
    """Markdown fences, extra text around a fence and an 'SQL:' label are removed."""
    assert clean_model_output(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (REFUSAL_REPLY, True),
        ("cannot answer: cannot answer from this data", True),
        ("  CANNOT ANSWER: no forecast data  ", True),
        ("SELECT 'CANNOT ANSWER'", False),
        ("SELECT 1", False),
    ],
)
def test_is_refusal(text: str, expected: bool) -> None:
    """Only replies that start with the refusal prefix count as refusals."""
    assert is_refusal(text) is expected


def test_generate_sql_returns_clean_sql() -> None:
    """The fenced reply is cleaned and marked as not refused."""
    generation = generate_sql(FakeLLM(["```sql\nSELECT 1;\n```"]), "sys", "user")
    assert generation.sql == "SELECT 1;"
    assert generation.refused is False
    assert generation.raw_text == "```sql\nSELECT 1;\n```"


def test_generate_sql_detects_refusal() -> None:
    """A refusal reply has no SQL."""
    generation = generate_sql(FakeLLM([REFUSAL_REPLY]), "sys", "user")
    assert generation.refused is True
    assert generation.sql is None
