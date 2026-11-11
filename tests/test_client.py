"""Tests for askdata.llm.client. A fake SDK object replaces the real Gemini API."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors

from askdata.llm import client as client_module
from askdata.llm.client import GeminiClient, LLMError, load_api_key


class FakeModels:
    """Stands in for sdk_client.models; returns queued replies or raises queued errors."""

    def __init__(self, outcomes: list) -> None:
        """outcomes: items are either reply strings or exceptions to raise, in order."""
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def generate_content(self, model: str, contents: str, config: object) -> SimpleNamespace:
        """Record the call and return (or raise) the next queued outcome."""
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)


def make_client(outcomes: list, cache_dir: Path | None, retries: int = 2) -> GeminiClient:
    """Build a GeminiClient around a fake SDK, with waiting switched off."""
    fake_sdk = SimpleNamespace(models=FakeModels(outcomes))
    return GeminiClient(
        model_name="fake-model",
        temperature=0.0,
        cache_dir=cache_dir,
        rate_limit_retries=retries,
        rate_limit_wait_seconds=1.0,
        sdk_client=fake_sdk,
        sleep=lambda seconds: None,
    )


def api_error(code: int) -> errors.APIError:
    """Create an SDK error with the given HTTP status code."""
    return errors.APIError(code, {"error": {"message": f"status {code}", "status": "X"}})


def test_returns_text_and_sends_settings(tmp_path: Path) -> None:
    """The reply text is returned; model, prompt and temperature are passed to the SDK."""
    client = make_client(["SELECT 1;"], cache_dir=None)
    response = client.generate("system rules", "Question: q\nSQL:")

    assert response.text == "SELECT 1;"
    assert response.from_cache is False
    call = client.sdk_client.models.calls[0]
    assert call["model"] == "fake-model"
    assert call["contents"] == "Question: q\nSQL:"
    assert call["config"].temperature == 0.0
    assert call["config"].system_instruction == "system rules"


def test_cache_avoids_second_call(tmp_path: Path) -> None:
    """The same request twice calls the API once; the second reply comes from disk."""
    client = make_client(["SELECT 1;"], cache_dir=tmp_path)
    first = client.generate("sys", "user")
    second = client.generate("sys", "user")

    assert first.text == second.text == "SELECT 1;"
    assert second.from_cache is True
    assert len(client.sdk_client.models.calls) == 1


def test_cache_survives_a_new_client(tmp_path: Path) -> None:
    """A new client (e.g. a new evaluation run) reuses replies saved on disk."""
    make_client(["SELECT 1;"], cache_dir=tmp_path).generate("sys", "user")
    second_client = make_client([], cache_dir=tmp_path)
    assert second_client.generate("sys", "user").text == "SELECT 1;"


def test_different_prompt_is_not_served_from_cache(tmp_path: Path) -> None:
    """A changed prompt gets a fresh call, never a stale cached reply."""
    client = make_client(["SELECT 1;", "SELECT 2;"], cache_dir=tmp_path)
    client.generate("sys", "user A")
    assert client.generate("sys", "user B").text == "SELECT 2;"


def test_rate_limit_is_retried_with_growing_wait() -> None:
    """Two 429 errors, then success: waits are 1s then 2s."""
    waits: list[float] = []
    client = make_client([api_error(429), api_error(429), "SELECT 1;"], cache_dir=None)
    client.sleep = waits.append

    assert client.generate("sys", "user").text == "SELECT 1;"
    assert waits == [1.0, 2.0]


def test_busy_model_503_is_retried() -> None:
    """A 503 'high demand' error is temporary, so it is retried like a 429."""
    waits: list[float] = []
    client = make_client([api_error(503), "SELECT 1;"], cache_dir=None)
    client.sleep = waits.append

    assert client.generate("sys", "user").text == "SELECT 1;"
    assert waits == [1.0]


def test_rate_limit_gives_up_after_max_retries() -> None:
    """After the allowed retries a clear LLMError is raised."""
    client = make_client([api_error(429)] * 3, cache_dir=None, retries=2)
    with pytest.raises(LLMError, match="429"):
        client.generate("sys", "user")
    assert len(client.sdk_client.models.calls) == 3


def test_other_errors_are_not_retried() -> None:
    """A 400 (bad request) fails immediately; retrying would not help."""
    client = make_client([api_error(400), "never used"], cache_dir=None)
    with pytest.raises(LLMError, match="400"):
        client.generate("sys", "user")
    assert len(client.sdk_client.models.calls) == 1


def test_empty_reply_raises() -> None:
    """A reply with no text is an error, not an empty SQL string."""
    client = make_client([None], cache_dir=None)
    with pytest.raises(LLMError, match="empty"):
        client.generate("sys", "user")


def test_failed_call_is_not_cached(tmp_path: Path) -> None:
    """Errors are never written to the cache."""
    client = make_client([api_error(400)], cache_dir=tmp_path)
    with pytest.raises(LLMError):
        client.generate("sys", "user")
    assert not list(tmp_path.glob("*.json"))


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without a key, the error message says how to set one."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Point the .env lookup at an empty folder so the real .env is not read.
    monkeypatch.setattr(client_module, "REPO_ROOT", tmp_path)
    with pytest.raises(LLMError, match=".env"):
        load_api_key()


def test_placeholder_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The placeholder from .env.example does not count as a key."""
    monkeypatch.setenv("GEMINI_API_KEY", "your-key-here")
    monkeypatch.setattr(client_module, "REPO_ROOT", tmp_path)
    with pytest.raises(LLMError):
        load_api_key()
