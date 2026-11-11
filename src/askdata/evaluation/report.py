"""Print README-ready Markdown tables from the latest evaluation run.

Run with:  python -m askdata.evaluation.report

Reads the newest eval/results/*_summary.csv (and the matching per-question CSV)
so every number in the README comes from a real results file, not from memory.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from askdata.config import load_config

SUMMARY_SUFFIX = "_summary.csv"
ACCURACY_GROUPS = ["all", "easy", "medium", "hard", "unanswerable"]


def find_latest_summary(results_dir: Path) -> Path | None:
    """Return the newest summary CSV, or None if there are no runs yet.

    File names start with the run id (YYYYMMDD-HHMMSS), so sorting by name
    also sorts by time.
    """
    summaries = sorted(results_dir.glob(f"*{SUMMARY_SUFFIX}"))
    if not summaries:
        return None
    return summaries[-1]


def results_file_for(summary_path: Path) -> Path:
    """Return the per-question CSV that belongs to a summary CSV."""
    return summary_path.with_name(summary_path.name.replace(SUMMARY_SUFFIX, ".csv"))


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV written by run_eval (UTF-8 with byte-order mark)."""
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def accuracy_cell(row: dict[str, str] | None) -> str:
    """Format one cell as 'passed/questions (accuracy%)', or a dash if the group is empty."""
    if row is None:
        return "-"
    return f"{row['passed']}/{row['questions']} ({row['accuracy_percent']}%)"


def accuracy_table(summary: list[dict[str, str]]) -> str:
    """Markdown table: one row per strategy, accuracy per group, rejections and latency."""
    header = (
        "| Strategy | All | Easy | Medium | Hard | Unanswerable "
        "| Validator rejections | Median latency (s) |"
    )
    lines = [header, "|---|---|---|---|---|---|---|---|"]

    strategies: list[str] = []
    for row in summary:
        if row["strategy"] not in strategies:
            strategies.append(row["strategy"])

    for strategy in strategies:
        by_group = {row["group"]: row for row in summary if row["strategy"] == strategy}
        cells = [accuracy_cell(by_group.get(group)) for group in ACCURACY_GROUPS]
        overall = by_group["all"]
        # Latency is only measured on answers that made a real model call.
        if overall["median_latency_seconds"]:
            latency = f"{overall['median_latency_seconds']} (n={overall['latency_measured_on']})"
        else:
            latency = "not measured (all cached)"
        lines.append(
            f"| {strategy} | " + " | ".join(cells) + f" | {overall['validator_rejections']} "
            f"| {latency} |"
        )
    return "\n".join(lines)


def failure_type_table(results: list[dict[str, str]]) -> str:
    """Markdown table of the failure types filled in by hand, with one example each."""
    failures = [row for row in results if row["passed"] != "True"]
    if not failures:
        return "No failures in this run."

    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for row in failures:
        failure_type = row["failure_type"].strip() or "(not classified yet)"
        counts[failure_type] += 1
        if failure_type not in examples:
            examples[failure_type] = f"{row['strategy']} / {row['id']}"

    lines = ["| Failure type | Count | Example (strategy / question id) |", "|---|---|---|"]
    for failure_type, count in counts.most_common():
        lines.append(f"| {failure_type} | {count} | {examples[failure_type]} |")
    return "\n".join(lines)


def main() -> None:
    """Print the tables for the latest run, or explain that there is none yet."""
    results_dir = load_config().paths.results
    summary_path = find_latest_summary(results_dir)
    if summary_path is None:
        print(f"No evaluation results in {results_dir} yet.")
        print("Run: python -m askdata.evaluation.run_eval")
        return

    summary = read_csv(summary_path)
    first = summary[0]
    print(f"Source: {summary_path.name} (model {first['model']}, run date {first['run_date']})\n")
    print(accuracy_table(summary))

    results_path = results_file_for(summary_path)
    if results_path.is_file():
        print("\nFailure types (fill the failure_type column in the CSV first):\n")
        print(failure_type_table(read_csv(results_path)))


if __name__ == "__main__":
    main()
