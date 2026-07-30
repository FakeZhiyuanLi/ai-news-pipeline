"""All model calls. The ONLY module that imports a model SDK.

Stages call json_call() with a PURPOSE — "rank", "report", "draft", "check" —
never a model name. config.MODELS maps purpose to (model, reasoning_effort),
so re-pointing a stage at a different model is a config edit and no stage
changes.

The client is built from base_url + model + key and knows nothing about any
particular vendor. A self-hosted vLLM or SGLang deployment exposes the same
OpenAI-compatible interface, so migrating off a hosted provider is a change
to config.BASE_URL and the slugs, and nothing more.

Every call leaves two traces:
  - one line in costs.jsonl (timestamp, run_id, purpose, model, tokens, cost)
  - the full prompt and raw response in runs/<run_id>/llm/<NN>-<purpose>.json,
    so a bad script can be traced to the exact prompt that produced it.

Prompt caching is automatic on repeated prefixes, so the stable instructions
go in a system message and the per-run material in the user message. The
cached fraction is reported in usage and costed at the cached rate.
"""

import datetime as _dt
import json
import logging
import os
import time

import openai

import config
import runs

log = logging.getLogger(__name__)

_client_instance: openai.OpenAI | None = None


class LLMError(RuntimeError):
    """A model call failed after retries, or returned unusable output."""


def _client() -> openai.OpenAI:
    global _client_instance
    if _client_instance is None:
        key = os.environ.get(config.API_KEY_ENV)
        if not key:
            raise LLMError(f"{config.API_KEY_ENV} is not set; add it to .env")
        _client_instance = openai.OpenAI(base_url=config.BASE_URL, api_key=key)
    return _client_instance


def _resolve(purpose: str) -> tuple[str, str]:
    try:
        return config.MODELS[purpose]
    except KeyError:
        raise LLMError(
            f"unknown purpose {purpose!r}; config.MODELS defines "
            f"{sorted(config.MODELS)}") from None


def cost_usd(model: str, tokens: dict) -> float:
    """Price one call from config.PRICES, charging the cached fraction of the
    input at the cached rate."""
    price = config.PRICES.get(model)
    if not price:
        return 0.0
    cached = tokens.get("cached_input", 0)
    fresh_input = max(0, tokens["input"] - cached)
    return round((fresh_input * price["input"]
                  + cached * price["cached_input"]
                  + tokens["output"] * price["output"]) / 1_000_000, 8)


def _usage_tokens(usage) -> dict:
    details = getattr(usage, "prompt_tokens_details", None)
    return {
        "input": getattr(usage, "prompt_tokens", 0) or 0,
        "output": getattr(usage, "completion_tokens", 0) or 0,
        "total": getattr(usage, "total_tokens", 0) or 0,
        "cached_input": (getattr(details, "cached_tokens", 0) or 0) if details else 0,
    }


def _is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    return status == 429 or (status is not None and 500 <= status < 600) \
        or isinstance(error, (openai.APIConnectionError, openai.APITimeoutError))


def json_call(purpose: str, prompt: str, schema: dict, *, run,
              temperature: float = config.DEFAULT_TEMPERATURE,
              system: str | None = None, label: str | None = None):
    """Make one structured-output call for `purpose` and return parsed JSON.

    purpose selects the model via config.MODELS. label only names the
    transcript file — callers making several calls for one purpose (bench
    variants, check retries) pass it to keep the transcripts apart, and it
    never affects which model runs.

    schema is a plain JSON Schema dict, sent as a strict json_schema response
    format — NOT json_object, which means "any valid JSON" and silently drops
    schema enforcement while finish_reason stays "stop".
    """
    model, effort = _resolve(purpose)
    max_tokens = config.MAX_TOKENS.get(purpose, config.DEFAULT_MAX_TOKENS)
    messages = []
    if system:
        # Stable prefix first: prompt caching keys on the leading tokens.
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = None
    for attempt in range(config.LLM_MAX_RETRIES):
        try:
            response = _client().chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": purpose.replace("-", "_"),
                                                 "schema": schema}},
                reasoning_effort=effort,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            break
        except Exception as e:
            if not _is_retryable(e) or attempt == config.LLM_MAX_RETRIES - 1:
                raise
            wait = min(config.LLM_BACKOFF_CAP_SECONDS,
                       config.LLM_BACKOFF_BASE_SECONDS * 2 ** attempt)
            log.warning("%s from %s (attempt %d/%d): backing off %.1fs",
                        getattr(e, "status_code", type(e).__name__), model,
                        attempt + 1, config.LLM_MAX_RETRIES, wait)
            time.sleep(wait)
    if response is None:  # pragma: no cover - loop always breaks or raises
        raise LLMError(f"no response from {model} after {config.LLM_MAX_RETRIES} attempts")

    choice = response.choices[0]
    tokens = _usage_tokens(response.usage)
    cost = cost_usd(model, tokens)
    transcript_path = run.write_llm_log(label or purpose, {
        "purpose": purpose,
        "model": model,
        "reasoning_effort": effort,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "system": system,
        "prompt": prompt,
        "schema": schema,
        "finish_reason": choice.finish_reason,
        "response_text": choice.message.content,
        "reasoning_content": getattr(choice.message, "reasoning_content", None),
        "tokens": tokens,
        "cost_usd": cost,
    })
    runs.append_jsonl(config.COSTS_FILE, {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "run_id": run.run_id,
        "purpose": purpose,
        "model": model,
        "tokens": tokens,
        "cost_usd": cost,
    })

    # Truncated output is invalid JSON, and the failure is worth naming
    # precisely: it means max_tokens was too small, not that the model erred.
    if choice.finish_reason == "length":
        raise LLMError(
            f"{model} hit the {max_tokens}-token ceiling for {purpose!r} and was "
            f"truncated mid-output (transcript: {transcript_path}). Raise "
            f"config.MAX_TOKENS[{purpose!r}] — reasoning tokens count against it.")

    try:
        return json.loads(choice.message.content)
    except (TypeError, json.JSONDecodeError) as e:
        raise LLMError(
            f"{model} returned non-JSON for {purpose!r} "
            f"(finish_reason={choice.finish_reason}, transcript: {transcript_path}): {e}"
        ) from e


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print(f"base_url: {config.BASE_URL}")
    for purpose, (model, effort) in config.MODELS.items():
        price = config.PRICES[model]
        print(f"  {purpose:7} -> {model.split('/')[-1]:16} effort={effort:4} "
              f"${price['input']}/${price['output']} per Mtok "
              f"(cached ${price['cached_input']}, as of {price['as_of']})")

    # Every model in MODELS must be priced, or costs.jsonl silently under-reports.
    for purpose, (model, _) in config.MODELS.items():
        assert model in config.PRICES, f"no price for {model} ({purpose})"

    tokens = {"input": 10_000, "output": 1_000, "cached_input": 9_000, "total": 11_000}
    expected = (1_000 * 1.25 + 9_000 * 0.10 + 1_000 * 5.00) / 1_000_000
    assert abs(cost_usd(config.DEEPSEEK_PRO, tokens) - expected) < 1e-9
    print(f"\ncosting with 90% cache hit: ${cost_usd(config.DEEPSEEK_PRO, tokens):.6f} "
          f"(vs ${cost_usd(config.DEEPSEEK_PRO, dict(tokens, cached_input=0)):.6f} uncached)")

    try:
        _resolve("nonexistent")
    except LLMError as e:
        print(f"unknown purpose rejected: {e}")
    print("llm OK (no live call made; json_call needs "
          f"{config.API_KEY_ENV})")
