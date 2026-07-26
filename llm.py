"""All model calls. The ONLY module that imports a model SDK.

Stages call json_call() and get parsed JSON back; swapping model or provider
touches this file alone. Rate limits are the main failure mode, so every call
goes through a per-model token bucket sized to the real RPM, waits are logged
(stalls should be visible, not mysterious), and 429s back off exponentially.

Every call leaves two traces:
  - one line in costs.jsonl (timestamp, run_id, model, tokens, purpose)
  - the full prompt and raw response in runs/<run_id>/llm/<NN>-<purpose>.json,
    so a bad script can be traced to the exact prompt that produced it.

Uses google-genai (the current SDK, v2.x). Structured output is requested via
GenerateContentConfig(response_json_schema=...), which takes a plain JSON
Schema dict — unlike the legacy google-generativeai SDK's generation_config.
"""

import datetime as _dt
import json
import logging
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

import config
import runs

log = logging.getLogger(__name__)

CHEAP_MODEL = config.CHEAP_MODEL
STRONG_MODEL = config.STRONG_MODEL

_client_instance: genai.Client | None = None


class LLMError(RuntimeError):
    """A model call failed after retries, or returned unusable output."""


def _client() -> genai.Client:
    global _client_instance
    if _client_instance is None:
        _client_instance = genai.Client()  # key from GEMINI_API_KEY / GOOGLE_API_KEY
    return _client_instance


class _TokenBucket:
    """Refills at rpm/60 tokens per second; acquire() blocks until a token
    is available and logs how long it waited."""

    def __init__(self, rpm: int, clock=time.monotonic, sleep=time.sleep):
        self.rate = rpm / 60.0
        self.capacity = float(rpm)
        self.tokens = float(rpm)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._updated) * self.rate)
        self._updated = now

    def acquire(self, label: str) -> None:
        self._refill()
        if self.tokens < 1.0:
            wait = (1.0 - self.tokens) / self.rate
            log.info("rate limit: waiting %.1fs for a %s slot", wait, label)
            self._sleep(wait)
            self._refill()
        self.tokens -= 1.0


_buckets = {model: _TokenBucket(rpm) for model, rpm in config.MODEL_RPM.items()}


def json_call(model: str, prompt: str, schema: dict, *, run, purpose: str,
              temperature: float = config.DEFAULT_TEMPERATURE):
    """Make one structured-output call and return the parsed JSON.

    schema is a plain JSON Schema dict. run is the current runs.Run — it
    receives the full transcript; the costs ledger gets one line.
    """
    if model not in _buckets:
        raise LLMError(f"unknown model {model!r}; known: {sorted(_buckets)}")

    response = None
    for attempt in range(config.LLM_MAX_RETRIES):
        _buckets[model].acquire(model)
        try:
            response = _client().models.generate_content(
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                ),
            )
            break
        except genai_errors.APIError as e:
            if e.code != 429 or attempt == config.LLM_MAX_RETRIES - 1:
                raise
            wait = min(config.LLM_BACKOFF_CAP_SECONDS,
                       config.LLM_BACKOFF_BASE_SECONDS * 2 ** attempt)
            log.warning("429 from %s (attempt %d/%d): backing off %.1fs",
                        model, attempt + 1, config.LLM_MAX_RETRIES, wait)
            time.sleep(wait)
    if response is None:  # pragma: no cover - loop always breaks or raises
        raise LLMError(f"no response from {model} after {config.LLM_MAX_RETRIES} attempts")

    usage = response.usage_metadata
    tokens = {
        "input": getattr(usage, "prompt_token_count", None) or 0,
        "output": getattr(usage, "candidates_token_count", None) or 0,
        "total": getattr(usage, "total_token_count", None) or 0,
    }
    transcript_path = run.write_llm_log(purpose, {
        "model": model,
        "purpose": purpose,
        "temperature": temperature,
        "prompt": prompt,
        "schema": schema,
        "response_text": response.text,
        "tokens": tokens,
    })
    runs.append_jsonl(config.COSTS_FILE, {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "run_id": run.run_id,
        "model": model,
        "tokens": tokens,
        "purpose": purpose,
    })

    try:
        return json.loads(response.text)
    except (TypeError, json.JSONDecodeError) as e:
        raise LLMError(
            f"{model} returned non-JSON for {purpose!r} "
            f"(transcript: {transcript_path}): {e}") from e


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"cheap model: {CHEAP_MODEL} ({config.MODEL_RPM[CHEAP_MODEL]} RPM)")
    print(f"strong model: {STRONG_MODEL} ({config.MODEL_RPM[STRONG_MODEL]} RPM)")

    # Exercise the limiter on a fake clock: 7 calls at 5 RPM should sail
    # through the first 5 and wait 12s for each of the next two.
    clock = [0.0]
    waits: list[float] = []

    def fake_sleep(s):
        waits.append(s)
        clock[0] += s

    bucket = _TokenBucket(rpm=5, clock=lambda: clock[0], sleep=fake_sleep)
    for _ in range(7):
        bucket.acquire("demo-5rpm")
    assert len(waits) == 2 and all(abs(w - 12.0) < 1e-6 for w in waits), waits
    print(f"token bucket: 7 acquisitions -> {len(waits)} waits of "
          f"{waits[0]:.0f}s each (simulated clock)")
    print("llm OK (no live call made; json_call needs GEMINI_API_KEY)")
