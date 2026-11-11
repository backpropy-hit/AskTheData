"""Fake LLM clients for tests: return pre-written replies, no network."""

from collections.abc import Callable

from askdata.llm.client import LLMError, LLMResponse


class FunctionLLM:
    """Replies by calling a function with (system_prompt, user_prompt).

    Useful when the reply should depend on the question or on whether the
    prompt is a retry, rather than on the order of calls.
    """

    model_name = "fake-llm"

    def __init__(self, reply_for: Callable[[str, str], str], from_cache: bool = False) -> None:
        """reply_for: function returning the reply text; from_cache: value to report."""
        self.reply_for = reply_for
        self.from_cache = from_cache
        self.user_prompts: list[str] = []

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Record the prompt and return reply_for(system_prompt, user_prompt)."""
        self.user_prompts.append(user_prompt)
        text = self.reply_for(system_prompt, user_prompt)
        return LLMResponse(text=text, from_cache=self.from_cache, latency_seconds=0.01)


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
