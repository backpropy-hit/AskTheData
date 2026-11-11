"""Prompt text for turning a question into SQL.

Two strategies are compared in the evaluation:
  * zero-shot: rules + schema only,
  * few-shot:  rules + schema + a few worked question/SQL examples.

The few-shot examples must never appear in eval/questions.jsonl, otherwise the
evaluation would measure memorisation. tests/test_prompts.py checks this.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# The exact reply the model must give when the data cannot answer a question.
REFUSAL_PREFIX = "CANNOT ANSWER"
REFUSAL_REPLY = f"{REFUSAL_PREFIX}: cannot answer from this data"


class PromptStrategy(StrEnum):
    """Which prompt variant to build."""

    ZERO_SHOT = "zero_shot"
    FEW_SHOT = "few_shot"


@dataclass(frozen=True)
class FewShotExample:
    """One worked example: a question and the reply we want for it."""

    question: str
    answer: str


FEW_SHOT_EXAMPLES: list[FewShotExample] = [
    # Simple lookup on one table, filtering by country codes.
    FewShotExample(
        question="How many asylum-seekers from Venezuela were waiting for a decision "
        "in Spain at the end of 2019?",
        answer=(
            "SELECT asylum_seekers\n"
            "FROM displacement\n"
            "WHERE year = 2019 AND origin_iso3 = 'VEN' AND asylum_iso3 = 'ESP';"
        ),
    ),
    # Aggregate over all rows, grouped by year.
    FewShotExample(
        question="What was the worldwide total of internally displaced people in each year?",
        answer=(
            "SELECT year, SUM(idps) AS total_idps\nFROM displacement\nGROUP BY year\nORDER BY year;"
        ),
    ),
    # One join to show country names instead of codes.
    FewShotExample(
        question="Which origin countries had more than 50,000 refugees living in Germany "
        "in 2022? Show their names, largest first.",
        answer=(
            "SELECT o.name AS origin_country, d.refugees\n"
            "FROM displacement AS d\n"
            "JOIN countries AS o ON o.iso3 = d.origin_iso3\n"
            "WHERE d.asylum_iso3 = 'DEU' AND d.year = 2022 AND d.refugees > 50000\n"
            "ORDER BY d.refugees DESC;"
        ),
    ),
    # Two joins to countries: one for the origin side, one for the asylum side.
    FewShotExample(
        question="In 2018, how many refugees from East Asia & Pacific countries were "
        "living in Europe & Central Asia countries?",
        answer=(
            "SELECT SUM(d.refugees) AS refugees\n"
            "FROM displacement AS d\n"
            "JOIN countries AS o ON o.iso3 = d.origin_iso3\n"
            "JOIN countries AS a ON a.iso3 = d.asylum_iso3\n"
            "WHERE d.year = 2018\n"
            "  AND o.region = 'East Asia & Pacific'\n"
            "  AND a.region = 'Europe & Central Asia';"
        ),
    ),
    # The tables have no age data, so the correct reply is a refusal.
    FewShotExample(
        question="What is the average age of refugees living in Uganda?",
        answer=REFUSAL_REPLY,
    ),
]

RULES = f"""You translate questions about humanitarian statistics into SQLite queries.

Rules:
1. Reply with exactly one SQLite SELECT statement (a WITH ... SELECT is allowed).
2. Use only the tables and columns in the schema below. Never change data.
3. Filter countries by their three-letter iso3 code. When the answer should show
   country names, join to the countries table.
4. Text values such as regions and income groups must match the listed values exactly.
5. If the tables cannot answer the question (for example a forecast, a year that is
   not in the data, or information that no column holds), reply with exactly:
   {REFUSAL_REPLY}
6. Reply with the SQL only: no explanation and no markdown code fences."""


def format_examples(examples: list[FewShotExample]) -> str:
    """Turn the few-shot examples into a text block for the prompt."""
    blocks = [f"Question: {example.question}\nSQL: {example.answer}" for example in examples]
    return "Examples:\n\n" + "\n\n".join(blocks)


def build_system_prompt(schema_text: str, strategy: PromptStrategy) -> str:
    """Build the system prompt: rules, schema, and examples for the few-shot strategy."""
    parts = [RULES, "Database schema:\n\n" + schema_text]
    if strategy == PromptStrategy.FEW_SHOT:
        parts.append(format_examples(FEW_SHOT_EXAMPLES))
    return "\n\n".join(parts)


def build_user_prompt(question: str) -> str:
    """Build the user message for a new question."""
    return f"Question: {question.strip()}\nSQL:"


def build_retry_prompt(question: str, previous_sql: str, error_message: str) -> str:
    """Build the user message that sends an error back to the model for one retry."""
    return (
        f"Question: {question.strip()}\n\n"
        f"Your previous SQL was:\n{previous_sql.strip()}\n\n"
        f"It failed with this error:\n{error_message.strip()}\n\n"
        "Write a corrected query that follows all the rules. Reply with the SQL only.\n"
        "SQL:"
    )
