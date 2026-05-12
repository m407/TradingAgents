"""OpenAI ``reasoning_effort`` is gated to reasoning models.

Non-reasoning OpenAI models (gpt-4.1, gpt-4o, ...) 400 with "Unsupported
parameter: 'reasoning.effort'". The client must drop the kwarg for those rather
than forward it and crash the run. The GPT-5 family and the o-series accept it.
"""

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.openai_client import (
    OpenAIClient,
    _supports_reasoning_effort,
)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-5.5", True), ("gpt-5.4", True), ("gpt-5.4-mini", True),
        ("gpt-5.5-pro", True), ("o1", True), ("o3-mini", True),
        ("gpt-4.1", False), ("gpt-4o", False), ("gpt-4o-mini", False),
        ("gpt-3.5-turbo", False),
    ],
)
def test_supports_reasoning_effort(model, expected):
    assert _supports_reasoning_effort(model) is expected


def _effort_on(model, monkeypatch):
    # A fake key lets get_llm() construct the client without a network call.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = OpenAIClient(model, provider="openai", reasoning_effort="low").get_llm()
    return getattr(llm, "reasoning_effort", None)


def test_reasoning_model_receives_effort(monkeypatch):
    assert _effort_on("gpt-5.4-mini", monkeypatch) == "low"


def test_non_reasoning_model_drops_effort(monkeypatch):
    # gpt-4.1 would 400 with reasoning_effort — it must be dropped.
    assert _effort_on("gpt-4.1", monkeypatch) is None


def _bare_graph(config):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = config
    return graph


def test_per_thinker_effort_overrides_shared_value_for_compatible_provider():
    graph = _bare_graph({
        "llm_provider": "openai_compatible",
        "openai_reasoning_effort": "medium",
        "quick_think_reasoning_effort": "low",
        "deep_think_reasoning_effort": "high",
    })

    assert graph._get_provider_kwargs("quick")["reasoning_effort"] == "low"
    assert graph._get_provider_kwargs("deep")["reasoning_effort"] == "high"


def test_per_thinker_effort_falls_back_to_shared_value():
    graph = _bare_graph({
        "llm_provider": "openai_compatible",
        "openai_reasoning_effort": "medium",
        "quick_think_reasoning_effort": None,
        "deep_think_reasoning_effort": None,
    })

    assert graph._get_provider_kwargs("quick")["reasoning_effort"] == "medium"
    assert graph._get_provider_kwargs("deep")["reasoning_effort"] == "medium"
