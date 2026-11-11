"""Tests for askdata.evaluation.report, using CSVs written by run_eval's own functions."""

from pathlib import Path

from askdata.evaluation.report import (
    accuracy_table,
    failure_type_table,
    find_latest_summary,
    read_csv,
    results_file_for,
)
from askdata.evaluation.run_eval import RESULT_COLUMNS, SUMMARY_COLUMNS, summarise, write_csv


def make_record(strategy: str, qid: str, difficulty: str, passed: bool, **extra) -> dict:
    """A minimal result record with every column run_eval writes."""
    record = {column: "" for column in RESULT_COLUMNS}
    record.update(
        {
            "run_id": "20260101-120000",
            "run_date": "2026-01-01",
            "model": "test-model",
            "strategy": strategy,
            "id": qid,
            "difficulty": difficulty,
            "answerable": difficulty != "hard",
            "passed": passed,
            "validator_rejections": 0,
            "latency_seconds": 2.0,
            "fully_cached": False,
        }
    )
    record.update(extra)
    return record


RECORDS = [
    make_record("zero_shot", "q1", "easy", True),
    make_record("zero_shot", "q2", "medium", False, failure_type="wrong join"),
    make_record("zero_shot", "q3", "hard", False),
    make_record("few_shot", "q1", "easy", True),
    make_record("few_shot", "q2", "medium", True),
    make_record("few_shot", "q3", "hard", False, failure_type="wrong join"),
]


def write_run(folder: Path, run_id: str) -> Path:
    """Write a results CSV and summary CSV like run_eval does; return the summary path."""
    write_csv(RECORDS, RESULT_COLUMNS, folder / f"{run_id}_test-model.csv")
    summary_path = folder / f"{run_id}_test-model_summary.csv"
    write_csv(summarise(RECORDS), SUMMARY_COLUMNS, summary_path)
    return summary_path


def test_no_results_returns_none(tmp_path: Path) -> None:
    """An empty results folder has no latest summary."""
    assert find_latest_summary(tmp_path) is None


def test_latest_summary_is_the_newest_run(tmp_path: Path) -> None:
    """With two runs, the later run id is picked."""
    write_run(tmp_path, "20260101-120000")
    newest = write_run(tmp_path, "20260202-090000")
    assert find_latest_summary(tmp_path) == newest
    assert results_file_for(newest).name == "20260202-090000_test-model.csv"


def test_accuracy_table_numbers_come_from_the_file(tmp_path: Path) -> None:
    """Cells show passed/total (percent) per group, read back from the CSV."""
    table = accuracy_table(read_csv(write_run(tmp_path, "20260101-120000")))
    lines = table.splitlines()
    assert lines[0].startswith("| Strategy | All | Easy |")
    assert "| zero_shot | 1/3 (33.3%) | 1/1 (100.0%) | 0/1 (0.0%) | 0/1 (0.0%) |" in table
    assert "| few_shot | 2/3 (66.7%) |" in table
    assert "2.0 (n=3)" in table


def test_failure_type_table_counts_and_examples(tmp_path: Path) -> None:
    """Hand-filled failure types are counted; empty ones show as not classified."""
    write_run(tmp_path, "20260101-120000")
    results = read_csv(tmp_path / "20260101-120000_test-model.csv")
    table = failure_type_table(results)
    assert "| wrong join | 2 | zero_shot / q2 |" in table
    assert "| (not classified yet) | 1 | zero_shot / q3 |" in table
