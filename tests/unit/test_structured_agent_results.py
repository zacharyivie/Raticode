from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest

from gofer.core.structured_output import (
    StructuredOutputError,
    parse_and_validate_output,
)

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "score", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "changes_requested"]},
        "score": {"type": "number"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "note": {"type": ["string", "null"]},
    },
}


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
@pytest.mark.parametrize("scheme", ["http", "file"])
def test_output_validation_never_retrieves_external_schemas(monkeypatch, keyword, scheme):
    calls = []

    def retrieve(request):
        calls.append(request)
        raise AssertionError("Schema validation must not retrieve external resources")

    monkeypatch.setattr(urllib.request, "urlopen", retrieve)
    schema = {"properties": {"result": {keyword: f"{scheme}://localhost/private/schema.json"}}}
    with pytest.raises(StructuredOutputError, match="Only local JSON Schema.*supported"):
        parse_and_validate_output('{"result":"value"}', schema)
    assert not calls


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_output_validation_preserves_local_schema_references(keyword):
    schema = {keyword: "#/$defs/result", "$defs": {"result": {"type": "string"}}}
    assert parse_and_validate_output('"value"', schema) == "value"
    with pytest.raises(StructuredOutputError, match="not of type 'string'"):
        parse_and_validate_output("42", schema)


@pytest.mark.parametrize("scheme", ["http", "file"])
def test_local_reference_under_external_schema_id_never_retrieves_resources(monkeypatch, scheme):
    calls = []

    def retrieve(request):
        calls.append(request)
        raise AssertionError("Schema validation must not retrieve external resources")

    monkeypatch.setattr(urllib.request, "urlopen", retrieve)
    schema = {
        "$defs": {"result": {"type": "string"}},
        "properties": {
            "result": {"$id": f"{scheme}://localhost/private/schema.json", "$ref": "#/$defs/result"}
        },
    }
    with pytest.raises(StructuredOutputError, match="cannot be resolved locally"):
        parse_and_validate_output('{"result":"value"}', schema)
    assert not calls


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_structured_output_rejects_nonfinite_numbers(raw):
    with pytest.raises(StructuredOutputError, match="finite"):
        parse_and_validate_output(f'{{"score":{raw}}}', {"type": "object"})


def test_structured_output_preserves_finite_numbers_and_literal_constant_strings():
    value = {"score": 1.5, "count": 123, "label": "NaN Infinity"}
    assert parse_and_validate_output(json.dumps(value), {"type": "object"}) == value


def test_deep_structured_output_returns_a_recoverable_error():
    with pytest.raises(StructuredOutputError, match="nesting"):
        parse_and_validate_output("[" * 2000 + "0" + "]" * 2000, {"type": "array"})


def test_recursive_schema_returns_a_recoverable_error():
    with pytest.raises(StructuredOutputError, match="recursion"):
        parse_and_validate_output('"value"', {"$ref": "#"})


def test_oversized_integer_returns_a_recoverable_error():
    with pytest.raises(StructuredOutputError, match="not valid JSON"):
        parse_and_validate_output("1" * 5000, {"type": "integer"})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"score":1,"findings":[]}', "required property"),
        ('{"verdict":null,"score":1,"findings":[]}', "not of type 'string'"),
        ('{"verdict":"approved","score":"high","findings":[]}', "not of type 'number'"),
    ],
)
def test_missing_null_and_wrong_type_fail_schema_validation(raw: str, message: str) -> None:
    with pytest.raises(StructuredOutputError, match=message):
        parse_and_validate_output(raw, REVIEW_SCHEMA)


@pytest.mark.parametrize(
    "reply",
    ['```json\n{"result": "value"}\n```', '  ```\n{"result": "value"}\n```\n'],
)
def test_output_validation_accepts_one_markdown_json_fence(reply):
    schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    assert parse_and_validate_output(reply, schema) == {"result": "value"}


def test_output_validation_rejects_prose_around_a_json_fence():
    with pytest.raises(StructuredOutputError, match="not valid JSON"):
        parse_and_validate_output('Here it is:\n```json\n{"result": "value"}\n```', {})


@pytest.mark.anyio
async def test_rattish_output_failure_carries_the_agent_reply():
    from gofer.core.agent import AgentResult
    from gofer.rattish.runtime import _structured_agent_output

    reply = "I need permission to read the prompt file."
    result = AgentResult(
        agent_id="ask", success=True, output=reply, exit_code=0, duration_seconds=0
    )

    # Without repair attempts the provider, settings, and context are never used.
    unused: Any = None
    output, _, error = await _structured_agent_output(
        result, {"type": "object"}, 0, unused, unused, unused, unused
    )

    assert output is None
    assert error is not None
    assert error.message.endswith(f"Agent reply:\n{reply}")
    assert error.details["response"] == reply
