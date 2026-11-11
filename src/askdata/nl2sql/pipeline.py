"""The full question-to-answer flow: generate -> validate -> run -> (retry once).

Run one question from the terminal:
    python -m askdata.nl2sql.pipeline "Which region hosted the most refugees in 2024?"
"""

from __future__ import annotations

import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from askdata.config import Config, load_config
from askdata.db.connection import QueryTimeoutError, connect_read_only, run_query
from askdata.db.schema_text import build_schema_text
from askdata.history import QueryHistory
from askdata.llm.client import GeminiClient, LLMClient, LLMError
from askdata.llm.prompts import (
    PromptStrategy,
    build_retry_prompt,
    build_system_prompt,
    build_user_prompt,
)
from askdata.nl2sql.generate import generate_sql
from askdata.safety.validator import validate_sql

# Possible values of PipelineResult.status.
STATUS_OK = "ok"  # query ran and returned rows (possibly zero rows)
STATUS_REFUSED = "refused"  # model said the data cannot answer the question
STATUS_REJECTED = "rejected"  # validator blocked the last query
STATUS_SQL_ERROR = "sql_error"  # the last query failed or timed out when run
STATUS_LLM_ERROR = "llm_error"  # the model call itself failed


@dataclass
class PipelineResult:
    """Everything that happened while answering one question."""

    question: str
    status: str
    sql: str | None = None  # the last SQL that was tried (after validation, if it passed)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0  # number of model calls made
    validator_rejections: int = 0  # how many generated queries the validator blocked
    latency_seconds: float = 0.0  # total wall-clock time for the whole question
    cached_calls: int = 0  # model calls answered from the on-disk cache

    @property
    def fully_cached(self) -> bool:
        """True if every model call came from the cache (so latency is not meaningful)."""
        return self.attempts > 0 and self.cached_calls == self.attempts


def list_tables(connection: sqlite3.Connection) -> set[str]:
    """Return the names of the user tables in the database (not SQLite's own tables)."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


class Pipeline:
    """Answers questions with one model, one prompt strategy and one database."""

    def __init__(
        self,
        client: LLMClient,
        connection: sqlite3.Connection,
        strategy: PromptStrategy,
        row_limit: int,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        """Set up the pipeline.

        Args:
            client: The LLM client (a fake one in tests).
            connection: A read-only database connection.
            strategy: Zero-shot or few-shot prompt.
            row_limit: LIMIT added by the validator when a query has none.
            timeout_seconds: Maximum run time for one query.
            max_retries: How many times an error is sent back to the model (0 = no retry).
        """
        self.client = client
        self.connection = connection
        self.strategy = strategy
        self.row_limit = row_limit
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.allowed_tables = list_tables(connection)
        # The schema text does not change, so the system prompt is built once.
        self.system_prompt = build_system_prompt(build_schema_text(connection), strategy)

    @classmethod
    def from_config(
        cls,
        config: Config,
        strategy: PromptStrategy = PromptStrategy.FEW_SHOT,
        max_retries: int | None = None,
    ) -> Pipeline:
        """Build a pipeline with the real Gemini client and the read-only database."""
        return cls(
            client=GeminiClient.from_config(config),
            connection=connect_read_only(config.paths.database),
            strategy=strategy,
            row_limit=config.query.row_limit,
            timeout_seconds=config.query.timeout_seconds,
            max_retries=config.query.sql_retries if max_retries is None else max_retries,
        )

    def ask(self, question: str) -> PipelineResult:
        """Answer one question. Never raises for model, validation or SQL problems."""
        start = time.perf_counter()
        result = PipelineResult(question=question, status=STATUS_LLM_ERROR)
        user_prompt = build_user_prompt(question)

        # One first attempt plus up to max_retries corrections.
        for attempt in range(1 + self.max_retries):
            result.attempts = attempt + 1
            error = self._attempt(user_prompt, result)
            if error is None:
                break  # success or refusal: nothing to correct
            result.error = error
            # Next round: show the model its last SQL and what went wrong.
            user_prompt = build_retry_prompt(question, result.sql or "", error)

        result.latency_seconds = time.perf_counter() - start
        return result

    def _attempt(self, user_prompt: str, result: PipelineResult) -> str | None:
        """Run one generate -> validate -> execute round and update `result`.

        Returns an error message that is worth sending back to the model,
        or None when there is nothing to retry (success, refusal, or LLM failure).
        """
        try:
            generation = generate_sql(self.client, self.system_prompt, user_prompt)
        except LLMError as error:
            result.status = STATUS_LLM_ERROR
            result.error = str(error)
            return None  # retrying with an error message would not fix an API failure

        if generation.from_cache:
            result.cached_calls += 1

        if generation.refused:
            result.status = STATUS_REFUSED
            result.sql = None
            result.error = None
            return None

        result.sql = generation.sql
        validation = validate_sql(generation.sql or "", self.allowed_tables, self.row_limit)
        if not validation.ok:
            result.status = STATUS_REJECTED
            result.validator_rejections += 1
            return f"The query was rejected by the safety check: {validation.error}"

        result.sql = validation.sql
        try:
            query_result = run_query(self.connection, validation.sql or "", self.timeout_seconds)
        except QueryTimeoutError as error:
            result.status = STATUS_SQL_ERROR
            return str(error)
        except sqlite3.Error as error:
            result.status = STATUS_SQL_ERROR
            return f"SQLite error: {error}"

        result.status = STATUS_OK
        result.columns = query_result.columns
        result.rows = query_result.rows
        result.error = None
        return None


def print_result(result: PipelineResult) -> None:
    """Print a pipeline result in a readable form for the terminal."""
    print(f"Question : {result.question}")
    print(
        f"Status   : {result.status}  (attempts: {result.attempts}, "
        f"rejections: {result.validator_rejections}, {result.latency_seconds:.1f}s)"
    )
    if result.sql:
        print("SQL      :")
        print("    " + result.sql.replace("\n", "\n    "))
    if result.error:
        print(f"Error    : {result.error}")
    if result.status == STATUS_OK:
        print(f"Rows     : {len(result.rows)}")
        print("    " + " | ".join(result.columns))
        for row in result.rows[:10]:
            print("    " + " | ".join(str(value) for value in row))
        if len(result.rows) > 10:
            print(f"    ... {len(result.rows) - 10} more rows")


def main() -> None:
    """Answer the question given on the command line and log it to the history file."""
    if len(sys.argv) < 2:
        print('Usage: python -m askdata.nl2sql.pipeline "your question"')
        sys.exit(1)
    question = " ".join(sys.argv[1:])
    config = load_config()
    pipeline = Pipeline.from_config(config)
    result = pipeline.ask(question)
    print_result(result)
    QueryHistory(config.paths.history).log(result, model=config.model.name)


if __name__ == "__main__":
    main()
