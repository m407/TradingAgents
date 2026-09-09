"""Literal effort forwarding at the application/SDK boundary, not server support."""

import os
import socket
from contextlib import nullcontext
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Never use user credentials, dotenv, tracing, or network in this suite."""
    def no_network(*args, **kwargs):
        raise AssertionError("Network access is forbidden in Anthropic unit tests")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    with patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1"}, clear=True):
        yield


@pytest.fixture
def mod(isolated_environment):
    from tradingagents.llm_clients import anthropic_client

    return anthropic_client


@pytest.fixture
def sdk_constructor(monkeypatch, mod):
    constructor = Mock(name="NormalizedChatAnthropic")
    monkeypatch.setattr(mod, "NormalizedChatAnthropic", constructor)
    return constructor


def model_warning(client):
    if client.validate_model():
        return nullcontext()
    return pytest.warns(RuntimeWarning, match="not in the known model list.*Continuing anyway")


MODELS = [
    "claude-haiku-4-5", "claude-haiku-5-0", "claude-haiku-4-7-preview",
    "claude-sonnet-4-5", "claude-sonnet-4-0",
    "claude-opus-4-5", "claude-opus-4-6", "claude-opus-4-7", "claude-sonnet-4-6",
    "claude-opus-5-0", "claude-opus-4-8", "claude-sonnet-5-0",
    "claude-sonnet-5", "claude-fable-5", "claude-mythos-5", "claude-mythos-preview",
    "claude-experimental-x", "gateway/custom-model",
]


@pytest.mark.unit
class TestEffortForwarding:
    @pytest.mark.parametrize("model", MODELS)
    @pytest.mark.parametrize("effort", ["low", "medium", "high", "none", "custom-effort", " HIGH "])
    def test_nonempty_effort_is_forwarded_literally(self, mod, sdk_constructor, model, effort):
        client = mod.AnthropicClient(model=model, effort=effort, api_key="dummy-key")
        with model_warning(client):
            result = client.get_llm()
        sdk_constructor.assert_called_once_with(model=model, effort=effort, api_key="dummy-key")
        assert result is sdk_constructor.return_value

    @pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-haiku-4-5", "gateway/custom-model"])
    @pytest.mark.parametrize("kwargs", [{}, {"effort": None}, {"effort": ""}], ids=["missing", "None", "empty"])
    def test_absent_effort_is_omitted(self, mod, sdk_constructor, model, kwargs):
        client = mod.AnthropicClient(model=model, api_key="dummy-key", **kwargs)
        with model_warning(client):
            client.get_llm()
        sdk_constructor.assert_called_once_with(model=model, api_key="dummy-key")

    def test_unknown_model_still_warns_and_continues(self, mod, sdk_constructor):
        client = mod.AnthropicClient(model="gateway/custom-model", effort="none", api_key="dummy-key")
        assert not client.validate_model()
        with pytest.warns(RuntimeWarning, match="provider 'anthropic'. Continuing anyway"):
            client.get_llm()
        assert sdk_constructor.call_args.kwargs["effort"] == "none"

    @pytest.mark.parametrize("effort", [None, "", "none", "custom-effort"])
    def test_other_kwargs_are_unchanged(self, mod, sdk_constructor, effort):
        callbacks, http_client, http_async_client = object(), object(), object()
        kwargs = dict(
            api_key="dummy-key", max_tokens=1024, timeout=30, max_retries=0,
            temperature=0, callbacks=callbacks, http_client=http_client,
            http_async_client=http_async_client,
        )
        client = mod.AnthropicClient(
            model="claude-haiku-4-5", base_url="https://gateway.invalid",
            effort=effort, **kwargs,
        )
        before = client.kwargs.copy()
        client.get_llm()
        expected = dict(model=client.model, base_url=client.base_url, **kwargs)
        if effort not in (None, ""):
            expected["effort"] = effort
        sdk_constructor.assert_called_once_with(**expected)
        assert client.kwargs == before

    def test_sdk_rejection_is_not_retried_without_effort(self, mod, sdk_constructor):
        error = ValueError("SDK rejected effort")
        sdk_constructor.side_effect = error
        client = mod.AnthropicClient(
            model="claude-opus-4-6", effort="custom-effort", api_key="dummy-key",
        )
        with model_warning(client), pytest.raises(ValueError) as caught:
            client.get_llm()
        assert caught.value is error
        sdk_constructor.assert_called_once_with(
            model="claude-opus-4-6", effort="custom-effort", api_key="dummy-key",
        )


@pytest.mark.unit
class TestResponseHandling:
    @pytest.mark.parametrize(
        "content, expected",
        [
            ("plain text", "plain text"),
            ([
                {"type": "thinking", "thinking": "private reasoning", "signature": "dummy"},
                {"type": "text", "text": "answer"},
                {"type": "tool_use", "id": "call-1", "name": "lookup", "input": {}},
                "more text",
            ], "answer\nmore text"),
        ],
    )
    def test_normalization_preserves_protocol_data(self, monkeypatch, mod, content, expected):
        from langchain_core.messages import AIMessage

        response = AIMessage(
            content=content,
            additional_kwargs={"reasoning_content": "protocol reasoning"},
            tool_calls=[{"id": "call-1", "name": "lookup", "args": {}}],
            response_metadata={"stop_reason": "tool_use"},
        )
        protocol = response.additional_kwargs.copy()
        tool_calls = response.tool_calls.copy()
        metadata = response.response_metadata.copy()
        invoke = Mock(return_value=response)
        monkeypatch.setattr(mod.ChatAnthropic, "invoke", invoke)
        # Bypass SDK construction entirely; only the application's invoke wrapper runs.
        llm = mod.NormalizedChatAnthropic.model_construct()
        config = {"tags": ["unit"]}
        result = llm.invoke("input", config=config, stop=["stop"])
        invoke.assert_called_once_with("input", config, stop=["stop"])
        assert result is response
        assert result.content == expected
        assert result.additional_kwargs == protocol
        assert result.tool_calls == tool_calls
        assert result.response_metadata == metadata

    def test_invoke_error_is_unchanged(self, monkeypatch, mod):
        error = RuntimeError("SDK invocation failed")
        invoke = Mock(side_effect=error)
        monkeypatch.setattr(mod.ChatAnthropic, "invoke", invoke)
        llm = mod.NormalizedChatAnthropic.model_construct()
        with pytest.raises(RuntimeError) as caught:
            llm.invoke("input")
        assert caught.value is error
        invoke.assert_called_once_with("input", None)
