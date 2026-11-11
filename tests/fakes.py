"""A fake LLM client for tests: returns pre-written replies, no network."""

from askdata.llm.client import LLMError, LLMResponse


class FakeLLM:
    """Returns queued replies in order and records every prompt it receives.

    A queued item that is an Exception is raised instead of returned,
    which lets tests simulate API failures.
    """

    model_name = "fake-llm"

    def __init__(self, replies: list) -> None:
        """replies: strings to return (or exceptions to raise), one per call."""
        self.replies = list(replies)
        self.user_prompts: list[str] = []
        self.system_prompts: list[str] = []

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Record the prompts and return the next queued reply."""
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        if not self.replies:
            raise LLMError("FakeLLM has no more replies queued.")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, from_cache=False, latency_seconds=0.01)
