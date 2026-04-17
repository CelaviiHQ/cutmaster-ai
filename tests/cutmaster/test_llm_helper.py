"""Tests for cutmaster.llm — model dispatch, retry, schema parsing.

No real Gemini calls: we monkeypatch ``get_gemini_client`` to return a
fake client whose ``generate_content`` returns canned responses.
"""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from celavii_resolve.cutmaster import llm


class DummyPlan(BaseModel):
    value: int


class DummyList(BaseModel):
    items: list[int]


@pytest.fixture
def fake_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(llm, "get_gemini_client", lambda: client)
    return client


def _canned(parsed: object | None = None, text: str = ""):
    """Build a mock response object mimicking google-genai."""
    resp = MagicMock()
    resp.parsed = parsed
    resp.text = text
    return resp


def test_model_for_reads_env_override(monkeypatch):
    monkeypatch.setenv("CELAVII_DIRECTOR_MODEL", "my-custom-model")
    assert llm.model_for("director") == "my-custom-model"


def test_model_for_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("CELAVII_DIRECTOR_MODEL", raising=False)
    assert llm.model_for("director") == llm.DEFAULTS["director"]


def test_no_api_key_raises(monkeypatch):
    monkeypatch.setattr(llm, "get_gemini_client", lambda: None)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        llm.call_structured("director", "prompt", DummyPlan)


def test_happy_path_parsed_pydantic(fake_client):
    fake_client.models.generate_content.return_value = _canned(parsed=DummyPlan(value=42))
    result = llm.call_structured("director", "prompt", DummyPlan)
    assert result.value == 42
    assert fake_client.models.generate_content.call_count == 1


def test_happy_path_falls_back_to_json_text(fake_client):
    # Simulate a model that doesn't populate .parsed
    fake_client.models.generate_content.return_value = _canned(
        parsed=None, text=json.dumps({"value": 7})
    )
    result = llm.call_structured("director", "prompt", DummyPlan)
    assert result.value == 7


def test_bare_array_wrapped_for_single_list_field(fake_client):
    # Lite models sometimes return [1, 2, 3] instead of {"items": [1, 2, 3]}
    fake_client.models.generate_content.return_value = _canned(
        parsed=None, text=json.dumps([1, 2, 3])
    )
    result = llm.call_structured("director", "prompt", DummyList)
    assert result.items == [1, 2, 3]


def test_validator_retry_feeds_errors_back(fake_client):
    # First call: invalid. Second call: valid.
    responses = [
        _canned(parsed=DummyPlan(value=1)),  # attempt 1 — validator rejects
        _canned(parsed=DummyPlan(value=99)),  # attempt 2 — validator accepts
    ]
    fake_client.models.generate_content.side_effect = responses

    validated_values: list[int] = []

    def validate(p: DummyPlan) -> list[str]:
        validated_values.append(p.value)
        return ["value must be >= 50"] if p.value < 50 else []

    result = llm.call_structured("director", "prompt", DummyPlan, validate=validate)
    assert result.value == 99
    assert validated_values == [1, 99]
    # The second call should have had the errors appended to the prompt
    second_call_prompt = fake_client.models.generate_content.call_args_list[1].kwargs["contents"][0]
    assert "value must be >= 50" in second_call_prompt


def test_validator_exhausts_retries_raises(fake_client):
    fake_client.models.generate_content.side_effect = [
        _canned(parsed=DummyPlan(value=1)) for _ in range(5)
    ]
    with pytest.raises(llm.AgentError, match="failed after"):
        llm.call_structured(
            "director",
            "prompt",
            DummyPlan,
            validate=lambda p: ["always wrong"],
            max_retries=3,
        )
    # Should have called exactly max_retries times, not more
    assert fake_client.models.generate_content.call_count == 3


def test_unparseable_json_raises(fake_client):
    fake_client.models.generate_content.return_value = _canned(parsed=None, text="not json {")
    with pytest.raises(llm.AgentError, match="non-JSON"):
        llm.call_structured("director", "prompt", DummyPlan)
