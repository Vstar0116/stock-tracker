"""Tests for natural-language screen translation. The OpenAI-compatible Groq
client is faked -- this suite must never make a real network call or need an
API key."""

import json

import pytest

from app.services import nl_screen


class _FakeFunction:
    def __init__(self, arguments: dict):
        self.arguments = json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, arguments: dict):
        self.function = _FakeFunction(arguments)


class _FakeMessage:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, tool_calls):
        self.message = _FakeMessage(tool_calls)


class _FakeResponse:
    def __init__(self, tool_calls):
        self.choices = [_FakeChoice(tool_calls)]


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeChat:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeClient:
    def __init__(self, response):
        self.chat = _FakeChat(response)


def _patch_client(monkeypatch, response):
    monkeypatch.setattr(nl_screen.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(nl_screen.openai, "OpenAI", lambda **kwargs: _FakeClient(response))


def test_translate_valid_rule(monkeypatch):
    response = _FakeResponse(
        [_FakeToolCall({"rule": {"type": "compare", "op": "gt", "field": "close", "value": 100}})]
    )
    _patch_client(monkeypatch, response)

    rule = nl_screen.translate_to_rule("close above 100")
    assert rule.type == "compare"
    assert rule.field == "close"
    assert rule.value == 100


def test_translate_rejects_unknown_field(monkeypatch):
    # Model ignored the field list -- must be rejected here, never reach compile_screen().
    response = _FakeResponse(
        [_FakeToolCall({"rule": {"type": "compare", "op": "gt", "field": "made_up_field", "value": 1}})]
    )
    _patch_client(monkeypatch, response)

    with pytest.raises(nl_screen.NlScreenError, match="failed validation"):
        nl_screen.translate_to_rule("nonsense")


def test_translate_no_tool_call_raises(monkeypatch):
    _patch_client(monkeypatch, _FakeResponse([]))

    with pytest.raises(nl_screen.NlScreenError, match="did not return"):
        nl_screen.translate_to_rule("anything")


def test_translate_without_api_key_raises(monkeypatch):
    monkeypatch.setattr(nl_screen.settings, "groq_api_key", None)

    with pytest.raises(nl_screen.NlScreenError, match="not configured"):
        nl_screen.translate_to_rule("anything")
