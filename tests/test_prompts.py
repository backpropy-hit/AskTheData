"""Tests for askdata.llm.prompts, including the evaluation-leakage check."""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from askdata.config import load_config
from askdata.db.connection import connect_read_only, run_query
from askdata.llm.prompts import (
    FEW_SHOT_EXAMPLES,
    REFUSAL_REPLY,
    PromptStrategy,
    build_retry_prompt,
    build_system_prompt,
    build_user_prompt,
)

SCHEMA = "CREATE TABLE countries (iso3 TEXT PRIMARY KEY);"


def normalise(text: str) -> str:
    """Lower-case, keep only letters and digits, collapse spaces.

    This makes "How many refugees?" and "how many  refugees" compare as equal.
    """
    words = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(words.split())


def load_eval_questions() -> list[dict]:
    """Return the evaluation questions, or skip the test if the file is not written yet."""
    path = load_config().paths.questions
    if not path.is_file():
        pytest.skip(f"{path} does not exist yet, so the leakage check cannot run.")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_few_shot_questions_are_not_in_eval_set() -> None:
    """No few-shot question may also be an evaluation question (that would inflate accuracy)."""
    eval_questions = {normalise(item["question"]) for item in load_eval_questions()}
    for example in FEW_SHOT_EXAMPLES:
        assert normalise(example.question) not in eval_questions, example.question


def test_few_shot_sql_is_not_an_eval_gold_query() -> None:
    """No few-shot SQL may be the same as a gold query either."""
    gold_queries = {
        normalise(item["gold_sql"]) for item in load_eval_questions() if item.get("gold_sql")
    }
    for example in FEW_SHOT_EXAMPLES:
        assert normalise(example.answer) not in gold_queries, example.question


def test_there_are_four_or_five_examples() -> None:
    """The design asks for 4 to 5 few-shot pairs."""
    assert 4 <= len(FEW_SHOT_EXAMPLES) <= 5


def test_few_shot_questions_are_unique() -> None:
    """Each example teaches something different."""
    questions = [normalise(example.question) for example in FEW_SHOT_EXAMPLES]
    assert len(questions) == len(set(questions))


def test_examples_include_a_refusal() -> None:
    """At least one example shows the exact refusal reply."""
    assert any(example.answer == REFUSAL_REPLY for example in FEW_SHOT_EXAMPLES)


def test_few_shot_sql_runs_on_the_schema(db_path: Path) -> None:
    """Every example SQL is valid against the real table and column names."""
    connection: sqlite3.Connection = connect_read_only(db_path)
    for example in FEW_SHOT_EXAMPLES:
        if example.answer == REFUSAL_REPLY:
            continue
        run_query(connection, example.answer, 5.0)  # raises on any SQL error


def test_zero_shot_prompt_has_rules_and_schema_but_no_examples() -> None:
    """Zero-shot: rules and schema only."""
    prompt = build_system_prompt(SCHEMA, PromptStrategy.ZERO_SHOT)
    assert SCHEMA in prompt
    assert REFUSAL_REPLY in prompt
    assert "Examples:" not in prompt
    for example in FEW_SHOT_EXAMPLES:
        assert example.question not in prompt


def test_few_shot_prompt_contains_every_example() -> None:
    """Few-shot: the zero-shot content plus every example question and answer."""
    prompt = build_system_prompt(SCHEMA, PromptStrategy.FEW_SHOT)
    assert SCHEMA in prompt
    assert "Examples:" in prompt
    for example in FEW_SHOT_EXAMPLES:
        assert example.question in prompt
        assert example.answer in prompt


def test_user_prompt_contains_question() -> None:
    """The user message holds the stripped question."""
    assert build_user_prompt("  How many?  ") == "Question: How many?\nSQL:"


def test_retry_prompt_contains_sql_and_error() -> None:
    """The retry message sends back the question, the failed SQL and the error."""
    prompt = build_retry_prompt("How many?", "SELECT x FROM y", "no such table: y")
    assert "How many?" in prompt
    assert "SELECT x FROM y" in prompt
    assert "no such table: y" in prompt
