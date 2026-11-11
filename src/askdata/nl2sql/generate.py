"""Turn a question into SQL with the LLM, and clean up the model's reply."""

from __future__ import annotations

import re
from dataclasses import dataclass

from askdata.llm.client import LLMClient
from askdata.llm.prompts import REFUSAL_PREFIX

# Matches a markdown code block such as ```sql ... ``` and captures its contents.
CODE_FENCE_PATTERN = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Generation:
    """What the model produced for one prompt.

    Exactly one of these is true: `refused` is True, or `sql` holds the query text.
    """

    sql: str | None
    refused: bool
    raw_text: str
    latency_seconds: float
    from_cache: bool


def clean_model_output(text: str) -> str:
    """Remove markdown fences and a leading 'SQL:' label from the model's reply.

    The prompt asks for plain SQL, but models sometimes wrap it in ```sql ... ```.
    """
    cleaned = text.strip()
    fence_match = CODE_FENCE_PATTERN.search(cleaned)
    if fence_match is not None:
        cleaned = fence_match.group(1).strip()
    if cleaned.lower().startswith("sql:"):
        cleaned = cleaned[len("sql:") :].strip()
    return cleaned


def is_refusal(text: str) -> bool:
    """Return True if the reply is the agreed 'cannot answer' message."""
    return text.strip().upper().startswith(REFUSAL_PREFIX)


def generate_sql(client: LLMClient, system_prompt: str, user_prompt: str) -> Generation:
    """Ask the model for SQL and return the cleaned result.

    Raises:
        LLMError: If the model call fails (passed through from the client).
    """
    response = client.generate(system_prompt, user_prompt)
    cleaned = clean_model_output(response.text)
    refused = is_refusal(cleaned)
    return Generation(
        sql=None if refused else cleaned,
        refused=refused,
        raw_text=response.text,
        latency_seconds=response.latency_seconds,
        from_cache=response.from_cache,
    )
