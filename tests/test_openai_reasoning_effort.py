"""Reasoning is forwarded at the application/SDK boundary, not validated here.

The SDK or server may reject values. Direct adapter calls preserve explicit
None/empty values too; only an absent kwarg is omitted.
"""

import os
import socket
from unittest.mock import patch

import pytest
from langchain_openai import ChatOpenAI

from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS, OpenAIClient


@pytest.fixture(autouse=True)
def isolated_boundary(monkeypatch, _dummy_api_keys):
    # Do not inherit real keys, endpoints, proxies, or tracing settings. The
    # suite runner disables dotenv before imports; this isolates each test too.
    with patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1"}, clear=True):
        for provider in OPENAI_COMPATIBLE_PROVIDERS:
            key_env = get_api_key_env(provider)
            if key_env:
                os.environ[key_env] = "placeholder"

        def no_network(*args, **kwargs):
            pytest.fail("Unexpected network access")

        monkeypatch.setattr(socket.socket, "connect", no_network)
        monkeypatch.setattr(socket.socket, "connect_ex", no_network)
        monkeypatch.setattr(socket, "getaddrinfo", no_network)
        # Leave the registry, chosen subclasses, and get_llm() logic intact.
        # Intercept the inherited SDK constructor before validation or I/O.
        with patch.object(ChatOpenAI, "__init__", autospec=True, return_value=None) as init:
            yield init


@pytest.mark.parametrize("model", [
    "gpt-5.4-mini", "o3-mini", "gpt-4.1", "gpt-4o", "arbitrary/model-id",
])
@pytest.mark.parametrize("effort", ["low", "none", "custom-effort", " HIGH ", None, ""])
def test_openai_models_forward_explicit_effort(model, effort, isolated_boundary):
    llm = OpenAIClient(model, reasoning_effort=effort).get_llm()
    isolated_boundary.assert_called_once_with(
        llm, model=model, api_key="placeholder", use_responses_api=True,
        reasoning_effort=effort,
    )


@pytest.mark.parametrize("provider", OPENAI_COMPATIBLE_PROVIDERS)
@pytest.mark.parametrize("effort", ["low", "none", "custom-effort", " HIGH ", None, ""])
def test_compatible_providers_forward_explicit_effort(provider, effort, isolated_boundary):
    model = "arbitrary/model-id"
    llm = OpenAIClient(
        model, provider=provider, base_url="https://gateway.invalid/v1",
        reasoning_effort=effort,
    ).get_llm()
    spec = OPENAI_COMPATIBLE_PROVIDERS[provider]
    assert type(llm) is spec.chat_class
    api_key = "placeholder" if get_api_key_env(provider) else spec.placeholder_key
    isolated_boundary.assert_called_once_with(
        llm, model=model, base_url="https://gateway.invalid/v1",
        api_key=api_key, reasoning_effort=effort,
    )


@pytest.mark.parametrize("provider", OPENAI_COMPATIBLE_PROVIDERS)
def test_absent_effort_is_not_added(provider, isolated_boundary):
    OpenAIClient(
        "arbitrary/model-id", provider=provider, base_url="https://gateway.invalid/v1",
    ).get_llm()
    isolated_boundary.assert_called_once()
    assert "reasoning_effort" not in isolated_boundary.call_args.kwargs


def test_unknown_model_still_warns_and_preserves_other_kwargs(isolated_boundary):
    callbacks = []
    with pytest.warns(RuntimeWarning, match="not in the known model list.*Continuing anyway"):
        llm = OpenAIClient(
            "arbitrary/model-id", reasoning_effort="none", temperature=0.3,
            max_tokens=321, timeout=12, max_retries=2, callbacks=callbacks,
        ).get_llm()
    isolated_boundary.assert_called_once_with(
        llm, model="arbitrary/model-id", api_key="placeholder", use_responses_api=True,
        reasoning_effort="none", temperature=0.3, max_tokens=321, timeout=12,
        max_retries=2, callbacks=callbacks,
    )
    assert isolated_boundary.call_args.kwargs["callbacks"] is callbacks


def test_sdk_rejection_is_not_retried_without_reasoning(isolated_boundary):
    error = ValueError("SDK rejected reasoning_effort")
    isolated_boundary.side_effect = error
    with pytest.raises(ValueError) as caught:
        OpenAIClient("gpt-4.1", reasoning_effort="custom-effort").get_llm()
    assert caught.value is error
    isolated_boundary.assert_called_once()
    assert isolated_boundary.call_args.kwargs["reasoning_effort"] == "custom-effort"
