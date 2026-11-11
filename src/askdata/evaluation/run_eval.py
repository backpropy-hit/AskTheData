"""Run the evaluation: every question x every prompt strategy, scored by execution accuracy.

Run with:  python -m askdata.evaluation.run_eval
           python -m askdata.evaluation.run_eval --strategies zero_shot few_shot

Writes two files to eval/results/ per run:
    <run id>_<model>.csv          one row per (strategy, question), incl. a blank
                                  failure_type column for manual failure analysis
    <run id>_<model>_summary.csv  accuracy, rejections and latency per strategy and group

The questions file (eval/questions.jsonl) is only read, never changed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from askdata.config import load_config
from askdata.db.connection import connect_read_only, run_query
from askdata.evaluation.metrics import score
from askdata.llm.client import GeminiClient, LLMClient
from askdata.llm.prompts import PromptStrategy
from askdata.nl2sql.pipeline import Pipeline

DIFFICULTIES = ("easy", "medium", "hard")
REQUIRED_FIELDS = ("id", "question", "gold_sql", "difficulty", "answerable")

RESULT_COLUMNS = [
    "run_id",
    "run_date",
    "model",
    "strategy",
    "id",
    "difficulty",
    "answerable",
    "question",
    "status",
    "passed",
    "reason",
    "attempts",
    "validator_rejections",
    "latency_seconds",
    "fully_cached",
    "generated_row_count",
    "gold_row_count",
    "generated_sql",
    "gold_sql",
    "error",
    "failure_type",  # left blank: filled in by hand during failure analysis
]

SUMMARY_COLUMNS = [
    "run_id",
    "run_date",
    "model",
    "strategy",
    "group",
    "questions",
    "passed",
    "accuracy_percent",
    "validator_rejections",
    "latency_measured_on",
    "mean_latency_seconds",
    "median_latency_seconds",
]


class QuestionFileError(ValueError):
    """Raised when eval/questions.jsonl is missing or has a malformed entry."""


@dataclass(frozen=True)
class EvalQuestion:
    """One evaluation question, as written in eval/questions.jsonl."""

    id: str
    question: str
    gold_sql: str | None
    difficulty: str
    answerable: bool


@dataclass(frozen=True)
class Strategy:
    """One way of running the pipeline that is compared in the evaluation."""

    name: str
    prompt: PromptStrategy
    max_retries: int


def make_strategies(sql_retries: int) -> list[Strategy]:
    """The three strategies from the proposal; the retry count comes from the config."""
    return [
        Strategy("zero_shot", PromptStrategy.ZERO_SHOT, max_retries=0),
        Strategy("few_shot", PromptStrategy.FEW_SHOT, max_retries=0),
        Strategy("few_shot_retry", PromptStrategy.FEW_SHOT, max_retries=sql_retries),
    ]


# ---------------------------------------------------------------- loading questions


def parse_question(data: dict[str, Any], line_number: int) -> EvalQuestion:
    """Check one JSON object from the questions file and turn it into an EvalQuestion."""
    where = f"line {line_number}"
    missing = [name for name in REQUIRED_FIELDS if name not in data]
    if missing:
        raise QuestionFileError(f"{where}: missing field(s) {missing}")

    if not isinstance(data["id"], str) or data["id"].strip() == "":
        raise QuestionFileError(f"{where}: 'id' must be a non-empty string")
    if not isinstance(data["question"], str) or data["question"].strip() == "":
        raise QuestionFileError(f"{where}: 'question' must be a non-empty string")
    if data["difficulty"] not in DIFFICULTIES:
        raise QuestionFileError(f"{where}: 'difficulty' must be one of {DIFFICULTIES}")
    if not isinstance(data["answerable"], bool):
        raise QuestionFileError(f"{where}: 'answerable' must be true or false")

    gold_sql = data["gold_sql"]
    if data["answerable"]:
        if not isinstance(gold_sql, str) or gold_sql.strip() == "":
            raise QuestionFileError(f"{where}: answerable questions need a 'gold_sql' query")
    else:
        # The gold SQL of an unanswerable question is never run, so it is ignored.
        gold_sql = None

    return EvalQuestion(
        id=data["id"].strip(),
        question=data["question"].strip(),
        gold_sql=gold_sql,
        difficulty=data["difficulty"],
        answerable=data["answerable"],
    )


def load_questions(path: Path) -> list[EvalQuestion]:
    """Read and check eval/questions.jsonl (one JSON object per line; blank lines skipped).

    Raises:
        QuestionFileError: If the file is missing, a line is not valid JSON,
            a field is wrong, or two questions share an id.
    """
    if not path.is_file():
        raise QuestionFileError(
            f"{path} not found. Write the evaluation questions first "
            "(fields: id, question, gold_sql, difficulty, answerable)."
        )

    questions: list[EvalQuestion] = []
    seen_ids: set[str] = set()
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if line.strip() == "":
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as error:
            raise QuestionFileError(f"line {line_number}: not valid JSON ({error})") from error
        if not isinstance(data, dict):
            raise QuestionFileError(f"line {line_number}: expected a JSON object")

        question = parse_question(data, line_number)
        if question.id in seen_ids:
            raise QuestionFileError(f"line {line_number}: duplicate id '{question.id}'")
        seen_ids.add(question.id)
        questions.append(question)

    if not questions:
        raise QuestionFileError(f"{path} contains no questions.")
    return questions


# ---------------------------------------------------------------- gold results


def run_gold_queries(
    questions: list[EvalQuestion],
    connection: sqlite3.Connection,
    timeout_seconds: float,
    row_limit: int,
) -> tuple[dict[str, list[tuple[Any, ...]]], list[str]]:
    """Run every gold query once. Returns (rows per question id, warnings).

    Gold queries are written by hand, so they are run directly on the read-only
    connection without the validator.

    Raises:
        ValueError: If a gold query fails, because then the question cannot be scored.
    """
    gold_rows: dict[str, list[tuple[Any, ...]]] = {}
    warnings: list[str] = []
    for question in questions:
        if not question.answerable or question.gold_sql is None:
            continue
        try:
            result = run_query(connection, question.gold_sql, timeout_seconds)
        except Exception as error:
            raise ValueError(f"Gold SQL for {question.id} failed: {error}") from error
        gold_rows[question.id] = result.rows

        # A generated query without LIMIT gets LIMIT row_limit, so it can never
        # return more rows than that. A larger gold result can then never match.
        if len(result.rows) > row_limit:
            warnings.append(
                f"{question.id}: gold query returns {len(result.rows)} rows, more than the "
                f"row limit of {row_limit}; a generated query without its own LIMIT "
                "cannot match it."
            )
        if len(result.rows) == 0:
            warnings.append(f"{question.id}: gold query returns no rows; check it by hand.")
    return gold_rows, warnings


# ---------------------------------------------------------------- running


def run_evaluation(
    questions: list[EvalQuestion],
    client: LLMClient,
    connection: sqlite3.Connection,
    strategies: list[Strategy],
    gold_rows: dict[str, list[tuple[Any, ...]]],
    row_limit: int,
    timeout_seconds: float,
    run_id: str,
    run_date: str,
) -> list[dict[str, Any]]:
    """Ask every question with every strategy and return one record per answer."""
    records: list[dict[str, Any]] = []
    for strategy in strategies:
        pipeline = Pipeline(
            client=client,
            connection=connection,
            strategy=strategy.prompt,
            row_limit=row_limit,
            timeout_seconds=timeout_seconds,
            max_retries=strategy.max_retries,
        )
        for number, question in enumerate(questions, start=1):
            result = pipeline.ask(question.question)
            gold = gold_rows.get(question.id)
            verdict = score(
                answerable=question.answerable,
                status=result.status,
                gold_sql=question.gold_sql,
                gold_rows=gold,
                predicted_rows=result.rows,
            )
            mark = "PASS" if verdict.passed else "FAIL"
            print(f"  [{strategy.name}] {number}/{len(questions)} {question.id}: {mark}")
            records.append(
                {
                    "run_id": run_id,
                    "run_date": run_date,
                    "model": client.model_name,
                    "strategy": strategy.name,
                    "id": question.id,
                    "difficulty": question.difficulty,
                    "answerable": question.answerable,
                    "question": question.question,
                    "status": result.status,
                    "passed": verdict.passed,
                    "reason": verdict.reason,
                    "attempts": result.attempts,
                    "validator_rejections": result.validator_rejections,
                    "latency_seconds": round(result.latency_seconds, 3),
                    "fully_cached": result.fully_cached,
                    "generated_row_count": len(result.rows),
                    "gold_row_count": len(gold) if gold is not None else "",
                    "generated_sql": result.sql or "",
                    "gold_sql": question.gold_sql or "",
                    "error": result.error or "",
                    "failure_type": "",
                }
            )
    return records


# ---------------------------------------------------------------- summarising


def summarise_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy, rejections and latency for one group of records.

    Latency is only averaged over answers that made at least one real model call;
    answers served entirely from the cache took ~0 seconds and would distort it.
    """
    total = len(records)
    passed = sum(1 for record in records if record["passed"])
    latencies = [record["latency_seconds"] for record in records if not record["fully_cached"]]
    return {
        "questions": total,
        "passed": passed,
        "accuracy_percent": round(100 * passed / total, 1) if total else "",
        "validator_rejections": sum(record["validator_rejections"] for record in records),
        "latency_measured_on": len(latencies),
        "mean_latency_seconds": round(statistics.mean(latencies), 2) if latencies else "",
        "median_latency_seconds": round(statistics.median(latencies), 2) if latencies else "",
    }


SUMMARY_GROUPS = ["all", "easy", "medium", "hard", "answerable", "unanswerable"]


def in_group(record: dict[str, Any], group: str) -> bool:
    """Return True if a result record belongs to a summary group."""
    if group == "all":
        return True
    if group in DIFFICULTIES:
        return record["difficulty"] == group
    if group == "answerable":
        return bool(record["answerable"])
    if group == "unanswerable":
        return not record["answerable"]
    raise ValueError(f"Unknown group: {group}")


def summarise(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One summary row per strategy for: all, each difficulty, answerable, unanswerable."""
    if not records:
        return []
    first = records[0]

    # Strategy names in the order they were run, without repeats.
    strategies: list[str] = []
    for record in records:
        if record["strategy"] not in strategies:
            strategies.append(record["strategy"])

    summary = []
    for strategy in strategies:
        strategy_records = [record for record in records if record["strategy"] == strategy]
        for group in SUMMARY_GROUPS:
            group_records = [record for record in strategy_records if in_group(record, group)]
            if not group_records:
                continue
            row = {
                "run_id": first["run_id"],
                "run_date": first["run_date"],
                "model": first["model"],
                "strategy": strategy,
                "group": group,
            }
            row.update(summarise_group(group_records))
            summary.append(row)
    return summary


# ---------------------------------------------------------------- output


def write_csv(rows: list[dict[str, Any]], columns: list[str], path: Path) -> None:
    """Write rows to a CSV file with the given column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig adds a byte-order mark so Excel shows names like "Türkiye" correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def safe_file_part(text: str) -> str:
    """Replace characters that are not safe in file names (e.g. '/' in a model id)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text)


def print_summary(summary: list[dict[str, Any]]) -> None:
    """Print accuracy by strategy and group as a small text table."""
    print("\nAccuracy by strategy and group")
    header = f"{'strategy':<16}{'group':<14}{'passed':>10}{'accuracy':>10}"
    header += f"{'rejected':>10}{'mean s':>9}{'median s':>10}"
    print(header)
    print("-" * len(header))
    for row in summary:
        passed = f"{row['passed']}/{row['questions']}"
        print(
            f"{row['strategy']:<16}{row['group']:<14}{passed:>10}"
            f"{row['accuracy_percent']:>9}%{row['validator_rejections']:>10}"
            f"{row['mean_latency_seconds']!s:>9}{row['median_latency_seconds']!s:>10}"
        )


def print_failures(records: list[dict[str, Any]]) -> None:
    """Print every failed answer with generated SQL next to gold SQL."""
    failures = [record for record in records if not record["passed"]]
    print(f"\nFailures: {len(failures)}")
    for record in failures:
        print(
            f"\n[{record['strategy']}] {record['id']} ({record['difficulty']}) "
            f"status={record['status']}: {record['reason']}"
        )
        print(f"  question : {record['question']}")
        print(f"  generated: {record['generated_sql'] or '(none)'}")
        print(f"  gold     : {record['gold_sql'] or '(unanswerable: should refuse)'}")
        if record["error"]:
            print(f"  error    : {record['error']}")


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point: run the evaluation and save the results."""
    config = load_config()
    all_strategies = make_strategies(config.query.sql_retries)
    parser = argparse.ArgumentParser(description="Run the Ask the Data evaluation.")
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=[strategy.name for strategy in all_strategies],
        default=[strategy.name for strategy in all_strategies],
        help="which strategies to run (default: all three)",
    )
    parser.add_argument(
        "--questions", type=Path, default=config.paths.questions, help="questions file"
    )
    args = parser.parse_args(argv)

    try:
        questions = load_questions(args.questions)
    except QuestionFileError as error:
        print(f"Cannot run the evaluation: {error}")
        sys.exit(1)
    strategies = [strategy for strategy in all_strategies if strategy.name in args.strategies]
    connection = connect_read_only(config.paths.database)
    client = GeminiClient.from_config(config)

    gold_rows, warnings = run_gold_queries(
        questions, connection, config.query.timeout_seconds, config.query.row_limit
    )
    for warning in warnings:
        print(f"WARNING {warning}")

    now = datetime.now()
    run_id = now.strftime("%Y%m%d-%H%M%S")
    run_date = now.date().isoformat()
    print(
        f"Run {run_id}: {len(questions)} questions x {len(strategies)} strategies, "
        f"model {client.model_name}"
    )

    records = run_evaluation(
        questions,
        client,
        connection,
        strategies,
        gold_rows,
        config.query.row_limit,
        config.query.timeout_seconds,
        run_id,
        run_date,
    )
    summary = summarise(records)

    base_name = f"{run_id}_{safe_file_part(client.model_name)}"
    results_path = config.paths.results / f"{base_name}.csv"
    summary_path = config.paths.results / f"{base_name}_summary.csv"
    write_csv(records, RESULT_COLUMNS, results_path)
    write_csv(summary, SUMMARY_COLUMNS, summary_path)

    print_summary(summary)
    print_failures(records)
    print(f"\nSaved {results_path}\nSaved {summary_path}")


if __name__ == "__main__":
    main()
