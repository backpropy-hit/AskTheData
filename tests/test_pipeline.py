"""Tests for askdata.nl2sql.pipeline with a fake LLM and the small test database."""

from pathlib import Path

from askdata.db.connection import connect_read_only
from askdata.llm.client import LLMError
from askdata.llm.prompts import REFUSAL_REPLY, PromptStrategy
from askdata.nl2sql.pipeline import (
    STATUS_LLM_ERROR,
    STATUS_OK,
    STATUS_REFUSED,
    STATUS_REJECTED,
    STATUS_SQL_ERROR,
    Pipeline,
)
from fakes import FakeLLM, FunctionLLM

GOOD_SQL = "SELECT refugees FROM displacement WHERE origin_iso3 = 'SOM' AND asylum_iso3 = 'KEN'"


def make_pipeline(db_path: Path, replies: list, max_retries: int = 1) -> Pipeline:
    """Build a pipeline over the test database with a fake LLM."""
    return Pipeline(
        client=FakeLLM(replies),
        connection=connect_read_only(db_path),
        strategy=PromptStrategy.FEW_SHOT,
        row_limit=100,
        timeout_seconds=5.0,
        max_retries=max_retries,
    )


def test_success_on_first_try(db_path: Path) -> None:
    """Valid SQL runs once and returns rows; a LIMIT was added by the validator."""
    pipeline = make_pipeline(db_path, [GOOD_SQL])
    result = pipeline.ask("How many Somali refugees are in Kenya?")

    assert result.status == STATUS_OK
    assert result.columns == ["refugees"]
    assert result.rows == [(400,)]
    assert result.attempts == 1
    assert result.error is None
    assert "LIMIT 100" in result.sql


def test_system_prompt_contains_schema(db_path: Path) -> None:
    """The prompt sent to the model contains the real schema of the database."""
    pipeline = make_pipeline(db_path, [GOOD_SQL])
    pipeline.ask("q")
    assert "CREATE TABLE displacement" in pipeline.client.system_prompts[0]


def test_refusal_is_not_retried(db_path: Path) -> None:
    """A refusal ends the pipeline without running any SQL."""
    pipeline = make_pipeline(db_path, [REFUSAL_REPLY])
    result = pipeline.ask("How many refugees will there be in 2040?")

    assert result.status == STATUS_REFUSED
    assert result.sql is None
    assert result.attempts == 1


def test_sql_error_is_sent_back_and_retry_succeeds(db_path: Path) -> None:
    """A wrong column name fails; the error goes back to the model; the fix runs."""
    pipeline = make_pipeline(db_path, ["SELECT refugee_count FROM displacement", GOOD_SQL])
    result = pipeline.ask("How many Somali refugees are in Kenya?")

    assert result.status == STATUS_OK
    assert result.attempts == 2
    assert result.rows == [(400,)]
    retry_prompt = pipeline.client.user_prompts[1]
    assert "no such column: refugee_count" in retry_prompt
    assert "SELECT refugee_count FROM displacement" in retry_prompt


def test_validator_rejection_is_sent_back_and_counted(db_path: Path) -> None:
    """A blocked query counts as a rejection and its reason is sent back."""
    pipeline = make_pipeline(db_path, ["DELETE FROM countries", GOOD_SQL])
    result = pipeline.ask("q")

    assert result.status == STATUS_OK
    assert result.validator_rejections == 1
    assert "safety check" in pipeline.client.user_prompts[1]


def test_gives_up_after_one_retry(db_path: Path) -> None:
    """Two bad queries in a row: status shows the last failure, only 2 calls made."""
    pipeline = make_pipeline(db_path, ["SELECT nope FROM countries", "SELECT nope2 FROM countries"])
    result = pipeline.ask("q")

    assert result.status == STATUS_SQL_ERROR
    assert result.attempts == 2
    assert "nope2" in result.error
    assert len(pipeline.client.user_prompts) == 2


def test_no_retry_when_max_retries_is_zero(db_path: Path) -> None:
    """The 'few-shot without retry' strategy makes exactly one call."""
    pipeline = make_pipeline(db_path, ["SELECT nope FROM countries", GOOD_SQL], max_retries=0)
    result = pipeline.ask("q")

    assert result.status == STATUS_SQL_ERROR
    assert result.attempts == 1
    assert len(pipeline.client.user_prompts) == 1


def test_rejected_twice_keeps_rejected_status(db_path: Path) -> None:
    """If the retry is blocked too, the status is 'rejected' with 2 rejections."""
    pipeline = make_pipeline(db_path, ["DROP TABLE countries", "SELECT * FROM sqlite_master"])
    result = pipeline.ask("q")

    assert result.status == STATUS_REJECTED
    assert result.validator_rejections == 2
    assert "sqlite_master" in result.error


def test_timeout_is_reported_as_sql_error(db_path: Path) -> None:
    """A query that runs too long is stopped and reported, not left hanging."""
    endless = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n"
    )
    pipeline = make_pipeline(db_path, [endless], max_retries=0)
    pipeline.timeout_seconds = 0.2
    result = pipeline.ask("q")

    assert result.status == STATUS_SQL_ERROR
    assert "longer than" in result.error


def test_llm_error_is_reported_not_raised(db_path: Path) -> None:
    """An API failure becomes status 'llm_error' and is not retried."""
    pipeline = make_pipeline(db_path, [LLMError("Gemini API error (503): busy")])
    result = pipeline.ask("q")

    assert result.status == STATUS_LLM_ERROR
    assert "503" in result.error
    assert result.attempts == 1


def test_cached_calls_are_counted(db_path: Path) -> None:
    """Cached replies are counted so the evaluation can leave them out of latency."""
    pipeline = make_pipeline(db_path, [GOOD_SQL])
    pipeline.client = FunctionLLM(lambda system, user: GOOD_SQL, from_cache=True)
    result = pipeline.ask("q")
    assert result.cached_calls == 1
    assert result.fully_cached is True


def test_fresh_calls_are_not_fully_cached(db_path: Path) -> None:
    """Replies from the real API are not marked as cached."""
    result = make_pipeline(db_path, [GOOD_SQL]).ask("q")
    assert result.cached_calls == 0
    assert result.fully_cached is False


def test_latency_is_recorded(db_path: Path) -> None:
    """Total time for the question is measured."""
    result = make_pipeline(db_path, [GOOD_SQL]).ask("q")
    assert result.latency_seconds > 0
