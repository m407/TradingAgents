"""Literal application-to-SDK arguments, not a promise of server acceptance."""

import os
import socket
from unittest import mock

import pytest

from tradingagents.llm_clients import google_client
from tradingagents.llm_clients.google_client import GoogleClient


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Real network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    with mock.patch.dict(os.environ, {}, clear=True):
        yield


def _captured_kwargs(model, **kwargs):
    # Arbitrary strings may be rejected by SDK validation; capture before it.
    with mock.patch.object(google_client, "NormalizedChatGoogleGenerativeAI") as sdk:
        result = GoogleClient(model, api_key="dummy-google-key", **kwargs).get_llm()
    sdk.assert_called_once()
    assert result is sdk.return_value
    assert sdk.call_args.args == ()
    return sdk.call_args.kwargs


@pytest.mark.parametrize(
    "model", ["gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.8-flash",
              "gemini-flash-latest", "gemini-pro-latest", "unknown-model", "custom-PRO"]
)
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "none", "custom-value", " HIGH "])
def test_literal_thinking_level(model, level):
    kw = _captured_kwargs(model, thinking_level=level)
    assert kw == {
        "model": model, "google_api_key": "dummy-google-key", "thinking_level": level,
        "timeout": 600,
    }


@pytest.mark.parametrize("kwargs", [{}, {"thinking_level": None}, {"thinking_level": ""}])
def test_absent_thinking_level_is_omitted(kwargs):
    kw = _captured_kwargs("gemini-3.5-flash", **kwargs)
    assert kw == {"model": "gemini-3.5-flash", "google_api_key": "dummy-google-key", "timeout": 600}


def test_unknown_model_still_warns_and_forwards():
    with pytest.warns(RuntimeWarning, match="not in the known model list.*Continuing anyway"):
        kw = _captured_kwargs("unknown-model", thinking_level="minimal")
    assert kw["thinking_level"] == "minimal"


def test_no_thinking_level_is_omitted():
    kw = _captured_kwargs("gemini-3.5-flash")
    assert "thinking_level" not in kw
    assert "thinking_budget" not in kw


@pytest.mark.unit
def test_a_gemini_call_times_out_like_the_other_providers():
    """The OpenAI and Anthropic SDKs give up after 600s; Gemini's waits forever
    unless told otherwise, so a stalled call would hang the run (#1417)."""
    assert _captured_kwargs("gemini-3.5-flash")["timeout"] == 600
    assert _captured_kwargs("gemini-3.5-flash", timeout=30)["timeout"] == 30


def test_sdk_rejection_is_not_retried_without_reasoning():
    error = ValueError("synthetic SDK validation error")
    with (
        mock.patch.object(google_client, "NormalizedChatGoogleGenerativeAI", side_effect=error) as sdk,
        pytest.raises(ValueError) as raised,
    ):
        GoogleClient("gemini-3.1-pro-preview", thinking_level="none").get_llm()
    assert raised.value is error
    sdk.assert_called_once_with(model="gemini-3.1-pro-preview", thinking_level="none", timeout=600)
