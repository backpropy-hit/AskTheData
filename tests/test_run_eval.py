"""Tests for askdata.evaluation.run_eval, using a scripted fake LLM and the test database."""

import csv
import json
from pathlib import Path

import pytest

from askdata.db.connection import connect_read_only
from askdata.evaluation.run_eval import (
    RESULT_COLUMNS,
    SUMMARY_COLUMNS,
    EvalQuestion,
    QuestionFileError,
    load_questions,
    make_strategies,
    run_evaluation,
    run_gold_queries,
    summarise,
    write_csv,
)
from askdata.llm.prompts import REFUSAL_REPLY
from fakes import FunctionLLM

Q_LOOKUP = "How many Somali refugees lived in Kenya in 2025?"
Q_TREND = "What was the total number of refugees in each year, oldest first?"
Q_FUTURE = "How many refugees will Kenya host in 2040?"

QUESTIONS = [
    EvalQuestion(
        id="t1",
        question=Q_LOOKUP,
        gold_sql="SELECT refugees FROM displacement "
        "WHERE year = 2025 AND origin_iso3 = 'SOM' AND asylum_iso3 = 'KEN'",
        difficulty="easy",
        answerable=True,
    ),
    EvalQuestion(
        id="t2",
        question=Q_TREND,
        gold_sql="SELECT year, SUM(refugees) FROM displacement GROUP BY year ORDER BY year",
        difficulty="medium",
        answerable=True,
    ),
    EvalQuestion(id="t3", question=Q_FUTURE, gold_sql=None, difficulty="hard", answerable=False),
]


def scripted_reply(system_prompt: str, user_prompt: str) -> str:
    """Pretend model with known mistakes, so every strategy has a known score.

    zero-shot:      t1 right, t2 wrong order, t3 runs SQL instead of refusing -> 1/3
    few-shot:       t1 right, t2 bad column (SQL error), t3 refuses           -> 2/3
    few-shot+retry: as few-shot, but the retry fixes t2                       -> 3/3
    """
    few_shot = "Examples:" in system_prompt
    if "Your previous SQL" in user_prompt:
        return "SELECT year, SUM(refugees) FROM displacement GROUP BY year ORDER BY year"
    if Q_LOOKUP in user_prompt:
        return (
            "SELECT refugees FROM displacement "
            "WHERE year = 2025 AND origin_iso3 = 'SOM' AND asylum_iso3 = 'KEN'"
        )
    if Q_TREND in user_prompt:
        if few_shot:
            return "SELECT year, SUM(refugee_total) FROM displacement GROUP BY year"
        return "SELECT year, SUM(refugees) FROM displacement GROUP BY year ORDER BY year DESC"
    if Q_FUTURE in user_prompt:
        return REFUSAL_REPLY if few_shot else "SELECT 1"
    raise AssertionError(f"Unexpected prompt: {user_prompt}")


def run_all(db_path: Path, from_cache: bool = False) -> list[dict]:
    """Run all three strategies on the three test questions."""
    connection = connect_read_only(db_path)
    gold_rows, _warnings = run_gold_queries(QUESTIONS, connection, 5.0, 100)
    return run_evaluation(
        questions=QUESTIONS,
        client=FunctionLLM(scripted_reply, from_cache=from_cache),
        connection=connection,
        strategies=make_strategies(sql_retries=1),
        gold_rows=gold_rows,
        row_limit=100,
        timeout_seconds=5.0,
        run_id="test-run",
        run_date="2026-01-01",
    )


def summary_row(summary: list[dict], strategy: str, group: str) -> dict:
    """Find one row of the summary table."""
    for row in summary:
        if row["strategy"] == strategy and row["group"] == group:
            return row
    raise AssertionError(f"No summary row for {strategy}/{group}")


# ---------- running and scoring ----------


def test_accuracy_per_strategy(db_path: Path) -> None:
    """The scripted model gives 1/3, 2/3 and 3/3 for the three strategies."""
    summary = summarise(run_all(db_path))
    assert summary_row(summary, "zero_shot", "all")["passed"] == 1
    assert summary_row(summary, "few_shot", "all")["passed"] == 2
    assert summary_row(summary, "few_shot_retry", "all")["passed"] == 3
    assert summary_row(summary, "few_shot", "all")["accuracy_percent"] == 66.7


def test_accuracy_per_group(db_path: Path) -> None:
    """Groups split by difficulty and by answerable/unanswerable."""
    summary = summarise(run_all(db_path))
    assert summary_row(summary, "few_shot", "medium")["passed"] == 0
    assert summary_row(summary, "few_shot", "unanswerable")["passed"] == 1
    assert summary_row(summary, "zero_shot", "unanswerable")["passed"] == 0
    assert summary_row(summary, "few_shot_retry", "answerable")["questions"] == 2


def test_record_details_for_a_failure(db_path: Path) -> None:
    """A failed record holds generated and gold SQL, the error, and a blank failure_type."""
    records = run_all(db_path)
    record = next(r for r in records if r["strategy"] == "few_shot" and r["id"] == "t2")
    assert record["passed"] is False
    assert record["status"] == "sql_error"
    assert "refugee_total" in record["generated_sql"]
    assert "ORDER BY year" in record["gold_sql"]
    assert "no such column" in record["error"]
    assert record["failure_type"] == ""
    assert record["model"] == "fake-llm"
    assert record["run_date"] == "2026-01-01"


def test_retry_strategy_uses_two_attempts(db_path: Path) -> None:
    """The retry strategy fixes t2 on its second attempt."""
    records = run_all(db_path)
    record = next(r for r in records if r["strategy"] == "few_shot_retry" and r["id"] == "t2")
    assert record["passed"] is True
    assert record["attempts"] == 2


def test_order_matters_when_gold_has_order_by(db_path: Path) -> None:
    """Zero-shot t2 returns the right rows in the wrong order, which fails."""
    records = run_all(db_path)
    record = next(r for r in records if r["strategy"] == "zero_shot" and r["id"] == "t2")
    assert record["status"] == "ok"
    assert record["passed"] is False
    assert "order" in record["reason"]


def test_cached_answers_are_left_out_of_latency(db_path: Path) -> None:
    """If every reply came from the cache, latency is not reported."""
    summary = summarise(run_all(db_path, from_cache=True))
    row = summary_row(summary, "few_shot", "all")
    assert row["latency_measured_on"] == 0
    assert row["mean_latency_seconds"] == ""


def test_fresh_answers_have_latency(db_path: Path) -> None:
    """Without the cache, mean and median latency are filled in."""
    row = summary_row(summarise(run_all(db_path)), "few_shot", "all")
    assert row["latency_measured_on"] == 3
    assert row["mean_latency_seconds"] != ""


def test_validator_rejections_are_counted(db_path: Path) -> None:
    """A blocked query shows up in the summary's rejection count."""

    def reply(system_prompt: str, user_prompt: str) -> str:
        """Always try to delete data."""
        return "DELETE FROM countries"

    connection = connect_read_only(db_path)
    records = run_evaluation(
        QUESTIONS[:1],
        FunctionLLM(reply),
        connection,
        make_strategies(sql_retries=1)[2:],  # few_shot_retry only
        {"t1": [(400,)]},
        100,
        5.0,
        "r",
        "d",
    )
    row = summary_row(summarise(records), "few_shot_retry", "all")
    assert row["validator_rejections"] == 2  # first try and the retry


# ---------- writing results ----------


def test_csv_files_have_expected_columns(db_path: Path, tmp_path: Path) -> None:
    """Results and summary CSVs are written with the documented columns."""
    records = run_all(db_path)
    results_path = tmp_path / "results" / "run.csv"
    summary_path = tmp_path / "results" / "run_summary.csv"
    write_csv(records, RESULT_COLUMNS, results_path)
    write_csv(summarise(records), SUMMARY_COLUMNS, summary_path)

    with results_path.open(encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    assert list(rows[0].keys()) == RESULT_COLUMNS
    assert len(rows) == 9  # 3 questions x 3 strategies

    with summary_path.open(encoding="utf-8-sig") as file:
        summary_rows = list(csv.DictReader(file))
    assert list(summary_rows[0].keys()) == SUMMARY_COLUMNS
    assert len(summary_rows) == 3 * 6  # 3 strategies x (all, 3 difficulties, 2 answerable groups)


# ---------- gold queries ----------


def test_gold_queries_run_and_warn_about_large_results(db_path: Path) -> None:
    """Gold rows are collected; results above the row limit produce a warning."""
    connection = connect_read_only(db_path)
    gold_rows, warnings = run_gold_queries(QUESTIONS, connection, 5.0, row_limit=1)
    assert gold_rows["t1"] == [(400,)]
    assert "t3" not in gold_rows  # unanswerable: no gold query
    assert any("t2" in warning and "row limit" in warning for warning in warnings)


def test_broken_gold_query_raises(db_path: Path) -> None:
    """A gold query with an error stops the run with the question id in the message."""
    broken = [EvalQuestion("bad1", "q", "SELECT nope FROM countries", "easy", True)]
    with pytest.raises(ValueError, match="bad1"):
        run_gold_queries(broken, connect_read_only(db_path), 5.0, 100)


# ---------- loading the questions file ----------


def write_jsonl(path: Path, items: list) -> Path:
    """Write items as JSON lines (strings are written as-is) and return the path."""
    lines = [item if isinstance(item, str) else json.dumps(item) for item in items]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


GOOD_ITEM = {
    "id": "q01",
    "question": "How many?",
    "gold_sql": "SELECT 1",
    "difficulty": "easy",
    "answerable": True,
}


def test_load_questions_reads_valid_file(tmp_path: Path) -> None:
    """A valid file loads; blank lines are skipped; unanswerable gold SQL is ignored."""
    unanswerable = {**GOOD_ITEM, "id": "q02", "answerable": False, "gold_sql": None}
    path = write_jsonl(tmp_path / "q.jsonl", [GOOD_ITEM, "", unanswerable])
    questions = load_questions(path)
    assert [question.id for question in questions] == ["q01", "q02"]
    assert questions[1].gold_sql is None


def test_load_questions_does_not_change_the_file(tmp_path: Path) -> None:
    """The questions file is only read."""
    path = write_jsonl(tmp_path / "q.jsonl", [GOOD_ITEM])
    before = path.read_bytes()
    load_questions(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({k: v for k, v in GOOD_ITEM.items() if k != "difficulty"}, "missing field"),
        ({**GOOD_ITEM, "difficulty": "very hard"}, "difficulty"),
        ({**GOOD_ITEM, "answerable": "yes"}, "answerable"),
        ({**GOOD_ITEM, "gold_sql": None}, "gold_sql"),
        ({**GOOD_ITEM, "question": "  "}, "question"),
        ("{not json", "not valid JSON"),
        ("[1, 2]", "JSON object"),
    ],
)
def test_load_questions_rejects_bad_entries(tmp_path: Path, item, message: str) -> None:
    """Each kind of mistake is reported with the line number."""
    path = write_jsonl(tmp_path / "q.jsonl", [item])
    with pytest.raises(QuestionFileError, match=message):
        load_questions(path)


def test_load_questions_rejects_duplicate_ids(tmp_path: Path) -> None:
    """Two questions with the same id are an error."""
    path = write_jsonl(tmp_path / "q.jsonl", [GOOD_ITEM, GOOD_ITEM])
    with pytest.raises(QuestionFileError, match="duplicate id 'q01'"):
        load_questions(path)


def test_load_questions_missing_file(tmp_path: Path) -> None:
    """A missing file says which fields to write."""
    with pytest.raises(QuestionFileError, match="not found"):
        load_questions(tmp_path / "missing.jsonl")
