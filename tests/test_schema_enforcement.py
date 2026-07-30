"""Strict json_schema enforcement, verified per model against the live API.

response_format={"type": "json_object"} means "any valid JSON" and silently
drops schema enforcement while finish_reason stays "stop", so the schema must
be sent as a json_schema block. Whether the provider actually ENFORCES that
block is per-model behaviour and must not be assumed to carry over from one
model to another — hence a live test across every model in config.MODELS.

Marked `live`: costs money and needs the API key. Run explicitly with
    pytest tests/test_schema_enforcement.py -m live
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

import config
import llm

CALLS_PER_MODEL = 20

# The enum deliberately EXCLUDES the natural answer. A model ignoring the
# schema says "BLUE"; a model honouring it must pick from the enum.
_COLORS = ["CRIMSON", "VERMILION", "CHARTREUSE"]
_SCHEMA = {
    "type": "object",
    "properties": {"color": {"type": "string", "enum": _COLORS}},
    "required": ["color"],
    "additionalProperties": False,
}

pytestmark = pytest.mark.live


def _models():
    seen = {}
    for purpose, (model, _effort) in config.MODELS.items():
        seen.setdefault(model, purpose)
    return sorted(seen.items())


@pytest.fixture(scope="module")
def live_client():
    if not os.environ.get(config.API_KEY_ENV):
        pytest.skip(f"{config.API_KEY_ENV} not set")
    return llm._client()


@pytest.mark.parametrize("model,purpose", _models())
def test_enum_is_enforced(model, purpose, live_client):
    def one(i):
        response = live_client.chat.completions.create(
            model=model,
            messages=[{"role": "user",
                       "content": f"What color is a clear midday sky? "
                                  f"Answer in JSON. (variation {i})"}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "color", "schema": _SCHEMA}},
            temperature=1.0,
            max_tokens=config.MAX_TOKENS.get(purpose, config.DEFAULT_MAX_TOKENS),
        )
        choice = response.choices[0]
        assert choice.finish_reason != "length", (
            f"{model} truncated; raise MAX_TOKENS[{purpose!r}]")
        return json.loads(choice.message.content)["color"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(one, range(CALLS_PER_MODEL)))

    violations = [c for c in results if c not in _COLORS]
    assert not violations, (
        f"{model} ignored the enum in {len(violations)}/{CALLS_PER_MODEL} calls: "
        f"{sorted(set(violations))}")


@pytest.mark.parametrize("model,purpose", _models())
def test_reasoning_effort_is_accepted(model, purpose, live_client):
    """Every model in MODELS is called with a reasoning_effort; a model that
    rejects the parameter would fail every call at runtime."""
    schema = {"type": "object", "properties": {"a": {"type": "integer"}},
              "required": ["a"], "additionalProperties": False}
    for effort in ("low", "high"):
        response = live_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "What is 17*23? JSON with key a."}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "a", "schema": schema}},
            reasoning_effort=effort,
            max_tokens=config.MAX_TOKENS.get(purpose, config.DEFAULT_MAX_TOKENS),
        )
        assert response.choices[0].finish_reason != "length"
        assert json.loads(response.choices[0].message.content)["a"] == 391
