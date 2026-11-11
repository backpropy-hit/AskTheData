"""A small wrapper around the Gemini API (google-genai SDK).

What it adds on top of the SDK:
  * fixed model and temperature from configs/config.yaml,
  * retry with a growing wait when the API says "too many requests" (HTTP 429)
    or "model overloaded / high demand" (HTTP 503),
  * an on-disk cache, so running the evaluation again does not repeat calls.

The pipeline only depends on the ``LLMClient`` protocol, so tests can pass a
fake client that returns fixed text without any network access.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from askdata.config import REPO_ROOT, Config

API_KEY_VARIABLE = "GEMINI_API_KEY"
# HTTP status codes that mean "try again later":
#   429 = too many requests (rate limit), 503 = model busy ("high demand").
# Any other error (bad request, unknown model, ...) will not fix itself, so it is not retried.
RETRYABLE_STATUSES = {429, 503}


class LLMError(Exception):
    """Raised when the model call fails or returns no usable text."""


@dataclass(frozen=True)
class LLMResponse:
    """Text returned by the model, plus where it came from and how long it took."""

    text: str
    from_cache: bool
    latency_seconds: float


class LLMClient(Protocol):
    """Anything that can turn a system prompt and a user prompt into text."""

    model_name: str

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Return the model's reply to the prompts."""
        ...


class ResponseCache:
    """Stores model replies as small JSON files, one per unique request."""

    def __init__(self, folder: Path) -> None:
        """Create the cache in `folder` (created on first write)."""
        self.folder = folder

    @staticmethod
    def make_key(model: str, temperature: float, system_prompt: str, user_prompt: str) -> str:
        """Return a SHA-256 hash that identifies one exact request.

        Any change to the model, temperature or prompt text gives a new key,
        so a stale reply is never reused for a different prompt.
        """
        request = json.dumps(
            {
                "model": model,
                "temperature": temperature,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            sort_keys=True,
        )
        return hashlib.sha256(request.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        """Return the file path used for a cache key."""
        return self.folder / f"{key}.json"

    def get(self, key: str) -> str | None:
        """Return the cached reply text for a key, or None if it is not cached."""
        path = self._path(key)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["text"]

    def put(self, key: str, text: str, request_info: dict[str, Any]) -> None:
        """Save a reply. The request details are stored too, for inspection."""
        self.folder.mkdir(parents=True, exist_ok=True)
        record = {
            **request_info,
            "text": text,
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        # Write to a temporary file first so an interrupted run never leaves
        # a half-written JSON file that would break the next read.
        temp_path = self._path(key).with_suffix(".tmp")
        temp_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_path, self._path(key))


def load_api_key() -> str:
    """Read the Gemini API key from the environment or the repo's .env file.

    Raises:
        LLMError: If no key is set.
    """
    load_dotenv(REPO_ROOT / ".env")
    key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if key == "" or key == "your-key-here":
        raise LLMError(
            f"{API_KEY_VARIABLE} is not set. Copy .env.example to .env and add your key."
        )
    return key


class GeminiClient:
    """Calls a Gemini model with fixed settings, retries on rate limits, caches replies."""

    def __init__(
        self,
        model_name: str,
        temperature: float,
        cache_dir: Path | None,
        rate_limit_retries: int,
        rate_limit_wait_seconds: float,
        sdk_client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Set up the client.

        Args:
            model_name: Gemini model id, e.g. "gemini-2.5-flash".
            temperature: Sampling temperature (0 for the most repeatable output).
            cache_dir: Folder for cached replies, or None to switch caching off.
            rate_limit_retries: How many times to retry after an HTTP 429 or 503.
            rate_limit_wait_seconds: First wait before retrying; doubles each time.
            sdk_client: A ready google-genai client. Tests pass a fake one here.
                If None, a real client is created with the key from .env.
            sleep: Function used to wait. Tests replace it to avoid real waiting.
        """
        self.model_name = model_name
        self.temperature = temperature
        self.cache = ResponseCache(cache_dir) if cache_dir is not None else None
        self.rate_limit_retries = rate_limit_retries
        self.rate_limit_wait_seconds = rate_limit_wait_seconds
        self.sleep = sleep
        if sdk_client is None:
            sdk_client = genai.Client(api_key=load_api_key())
        self.sdk_client = sdk_client

    @classmethod
    def from_config(cls, config: Config, use_cache: bool = True) -> GeminiClient:
        """Create a client from the project config."""
        return cls(
            model_name=config.model.name,
            temperature=config.model.temperature,
            cache_dir=config.paths.llm_cache if use_cache else None,
            rate_limit_retries=config.model.rate_limit_retries,
            rate_limit_wait_seconds=config.model.rate_limit_wait_seconds,
        )

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Return the model's reply, from the cache if this exact request was made before."""
        start = time.perf_counter()
        key = ResponseCache.make_key(self.model_name, self.temperature, system_prompt, user_prompt)

        if self.cache is not None:
            cached_text = self.cache.get(key)
            if cached_text is not None:
                return LLMResponse(
                    text=cached_text,
                    from_cache=True,
                    latency_seconds=time.perf_counter() - start,
                )

        text = self._call_with_retry(system_prompt, user_prompt)

        if self.cache is not None:
            request_info = {
                "model": self.model_name,
                "temperature": self.temperature,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
            self.cache.put(key, text, request_info)

        return LLMResponse(text=text, from_cache=False, latency_seconds=time.perf_counter() - start)

    def _call_with_retry(self, system_prompt: str, user_prompt: str) -> str:
        """Call the API, waiting and retrying on a rate limit (429) or busy model (503)."""
        wait_seconds = self.rate_limit_wait_seconds
        attempt = 0
        while True:
            try:
                return self._call_once(system_prompt, user_prompt)
            except errors.APIError as error:
                is_temporary = error.code in RETRYABLE_STATUSES
                if not is_temporary or attempt >= self.rate_limit_retries:
                    raise LLMError(f"Gemini API error ({error.code}): {error.message}") from error
                attempt += 1
                print(
                    f"Gemini API busy ({error.code}); waiting {wait_seconds:.0f}s "
                    f"(retry {attempt} of {self.rate_limit_retries})"
                )
                self.sleep(wait_seconds)
                wait_seconds *= 2

    def _call_once(self, system_prompt: str, user_prompt: str) -> str:
        """Make one API call and return the reply text."""
        response = self.sdk_client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
            ),
        )
        # response.text is None when the model returns no text (e.g. blocked output).
        if not response.text:
            raise LLMError("The model returned an empty reply.")
        return response.text
