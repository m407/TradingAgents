"""Offline graph connection resolution; adapters and agent factories are boundaries."""

import socket
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.graph import setup as graph_setup_module, trading_graph as graph_module
from tradingagents.llm_clients.factory import create_llm_client as real_factory
from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS

pytestmark = pytest.mark.unit
MISSING = object()


@pytest.fixture(scope="session", autouse=True)
def offline_session():
    """Keep network forbidden through the remaining acceptance suites as well."""
    def forbidden(*args, **kwargs):
        pytest.fail("Network access is forbidden in graph acceptance tests")

    with pytest.MonkeyPatch.context() as patch:
        for name in ("connect", "connect_ex"):
            patch.setattr(socket.socket, name, forbidden)
        for name in ("create_connection", "getaddrinfo"):
            patch.setattr(socket, name, forbidden)
        yield


@pytest.fixture
def harness(monkeypatch, tmp_path):
    config = {
        "llm_provider": "openai", "backend_url": "https://common.invalid/v1",
        "deep_think_llm": "custom-deep", "quick_think_llm": "gemini-custom-pro",
        "temperature": "0.2", "llm_max_retries": "3", "max_tokens": "4096",
        "max_debate_rounds": 1, "max_risk_discuss_rounds": 1,
        "data_cache_dir": str(tmp_path / "cache"), "results_dir": str(tmp_path / "results"),
    }
    clients = [MagicMock(), MagicMock()]
    calls = []

    def substitute(**kwargs):
        calls.append(kwargs)
        # Use the actual factory's unsupported-provider branch, not a permissive
        # mock that could conceal graph fallback or invent different errors.
        if kwargs["provider"] == "not-a-provider":
            return real_factory(**kwargs)
        return clients[len(calls) - 1]

    monkeypatch.setattr(graph_module, "create_llm_client", substitute)
    components = {}
    for name in ("set_config", "TradingMemoryLog", "GraphSetup", "Propagator",
                 "Reflector"):
        components[name] = MagicMock()
        monkeypatch.setattr(graph_module, name, components[name])
    return SimpleNamespace(config=config, clients=clients, calls=calls, components=components)


def construct(harness):
    before = deepcopy(harness.config)
    callbacks = [object()]
    graph = graph_module.TradingAgentsGraph(config=harness.config, callbacks=callbacks)
    assert harness.config == before
    assert graph.config is harness.config
    assert graph.deep_thinking_llm is harness.clients[0].get_llm.return_value
    assert graph.quick_thinking_llm is harness.clients[1].get_llm.return_value
    for index, call in enumerate(harness.calls):
        tier = ("deep", "quick")[index]
        assert call["model"] == harness.config[f"{tier}_think_llm"]
        assert call["callbacks"] is callbacks
        assert call["temperature"] == 0.2
        assert call["max_retries"] == 3
        token_key = "max_output_tokens" if call["provider"] == "google" else "max_tokens"
        assert call[token_key] == 4096
        assert ({"max_tokens", "max_output_tokens"} - {token_key}).isdisjoint(call)
        harness.clients[index].get_llm.assert_called_once_with()
    harness.components["Reflector"].assert_called_once_with(graph.quick_thinking_llm)
    return graph


@pytest.mark.parametrize("providers", [("OpenAI", "Google"), ("Google", "OpenAI")])
def test_mixed_connections_reasoning_and_assignments(harness, monkeypatch, providers):
    for tier, provider in zip(("deep", "quick"), providers, strict=True):
        harness.config[f"{tier}_think_llm_provider"] = provider
        harness.config[f"{tier}_think_llm_backend_url"] = (
            "http://localhost:8317/v1beta" if provider == "Google" else "http://localhost:8317/v1"
        )
    harness.config.update(openai_reasoning_effort="none", google_thinking_level="minimal")

    # Exercise real GraphSetup allocation, substituting only agent factories.
    monkeypatch.setattr(graph_module, "GraphSetup", graph_setup_module.GraphSetup)

    def node(state):
        return {}
    factories = {}
    for role in ("market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst",
                 "bull_researcher", "bear_researcher", "research_manager", "trader",
                 "aggressive_debator", "neutral_debator", "conservative_debator", "portfolio_manager"):
        factories[role] = MagicMock(return_value=node)
        monkeypatch.setattr(graph_setup_module, f"create_{role}", factories[role])
    monkeypatch.setattr(graph_setup_module, "create_msg_delete", MagicMock(return_value=node))
    graph = construct(harness)
    for tier, provider, call in zip(("deep", "quick"), providers, harness.calls, strict=True):
        assert call["provider"] == provider.lower()
        assert call["base_url"] == harness.config[f"{tier}_think_llm_backend_url"]
        expected = {"thinking_level": "minimal"} if provider == "Google" else {"reasoning_effort": "none"}
        assert {k: call[k] for k in ("thinking_level", "reasoning_effort", "effort") if k in call} == expected
    for role, factory in factories.items():
        llm = graph.deep_thinking_llm if role in ("research_manager", "portfolio_manager") else graph.quick_thinking_llm
        factory.assert_called_once_with(llm)
    assert graph.selected_analysts == ("market", "social", "news", "fundamentals")


@pytest.mark.parametrize("tier", ["deep", "quick"])
@pytest.mark.parametrize("provider", [MISSING, None, "", "Google"])
@pytest.mark.parametrize("url", [MISSING, None, "", "https://tier.invalid/v1beta"])
def test_independent_overrides(harness, tier, provider, url):
    for suffix, value in (("provider", provider), ("backend_url", url)):
        if value is not MISSING:
            harness.config[f"{tier}_think_llm_{suffix}"] = value
    construct(harness)
    for current, call in zip(("deep", "quick"), harness.calls, strict=True):
        assert call["provider"] == ("google" if current == tier and provider == "Google" else "openai")
        assert call["base_url"] == (url if current == tier and url is not MISSING and url else harness.config["backend_url"])


def test_same_provider_distinct_urls(harness):
    harness.config.update(deep_think_llm_backend_url="https://deep.invalid/v1",
                          quick_think_llm_backend_url="https://quick.invalid/v1")
    construct(harness)
    assert [call["provider"] for call in harness.calls] == ["openai", "openai"]
    assert [call["base_url"] for call in harness.calls] == ["https://deep.invalid/v1", "https://quick.invalid/v1"]


@pytest.mark.parametrize("tier", ["deep", "quick"])
@pytest.mark.parametrize("common_url", [MISSING, None, ""])
def test_absent_urls_do_not_borrow_other_tier(harness, tier, common_url):
    harness.config.pop("backend_url")
    if common_url is not MISSING:
        harness.config["backend_url"] = common_url
    other = "quick" if tier == "deep" else "deep"
    harness.config[f"{other}_think_llm_backend_url"] = "https://other.invalid/v1"
    harness.config[f"{tier}_think_llm_provider"] = "google"
    construct(harness)
    assert harness.calls[0 if tier == "deep" else 1]["base_url"] is None


@pytest.mark.parametrize("tier", ["deep", "quick"])
def test_unknown_provider_keeps_real_factory_error(harness, tier):
    harness.config[f"{tier}_think_llm_provider"] = "NOT-A-PROVIDER"
    before = deepcopy(harness.config)
    with pytest.raises(ValueError, match="Unsupported LLM provider: not-a-provider"):
        graph_module.TradingAgentsGraph(config=harness.config)
    assert harness.calls[-1]["provider"] == "not-a-provider"
    assert len(harness.calls) == (1 if tier == "deep" else 2)
    assert harness.config == before


def test_absent_generic_url_preserves_adapter_error(harness, monkeypatch):
    harness.config.update(deep_think_llm_provider="openai_compatible", backend_url="",
                          quick_think_llm_backend_url="https://other.invalid/v1")
    calls = []

    def substitute(**kwargs):
        calls.append(kwargs)
        # This real adapter rejects absent URLs before constructing any SDK.
        if kwargs["provider"] == "openai_compatible":
            return real_factory(**kwargs)
        return harness.clients[1]

    monkeypatch.setattr(graph_module, "create_llm_client", substitute)
    with pytest.raises(ValueError, match="requires a base_url"):
        graph_module.TradingAgentsGraph(config=harness.config)
    assert calls[0]["base_url"] is None


def test_reasoning_rejection_is_not_retried_without_parameter(harness):
    harness.config.update(deep_think_llm_provider="google", deep_think_reasoning_effort="custom")
    harness.clients[0].get_llm.side_effect = ValueError("reasoning rejected")
    with pytest.raises(ValueError, match="reasoning rejected"):
        graph_module.TradingAgentsGraph(config=harness.config)
    assert len(harness.calls) == 2
    assert harness.calls[0]["thinking_level"] == "custom"
    harness.clients[0].get_llm.assert_called_once_with()
    harness.clients[1].get_llm.assert_not_called()


@pytest.mark.parametrize("provider", [*OPENAI_COMPATIBLE_PROVIDERS, "azure", "anthropic", "google", "bedrock"])
@pytest.mark.parametrize("tier_value", [MISSING, None, "", "none", "minimal", " custom-value "])
def test_effective_provider_reasoning(harness, provider, tier_value):
    harness.config.update(llm_provider="google", deep_think_llm_provider=provider.upper(),
                          openai_reasoning_effort="high", anthropic_effort="medium",
                          google_thinking_level="low")
    if tier_value is not MISSING:
        harness.config["deep_think_reasoning_effort"] = tier_value
    graph = construct(harness)
    parameter, common = (
        ("thinking_level", "low") if provider == "google" else
        ("effort", "medium") if provider == "anthropic" else
        (None, None) if provider == "bedrock" else ("reasoning_effort", "high")
    )
    expected = {parameter: tier_value if tier_value is not MISSING and tier_value else common} if parameter else {}
    assert {k: harness.calls[0][k] for k in ("reasoning_effort", "thinking_level", "effort") if k in harness.calls[0]} == expected
    assert harness.calls[1]["thinking_level"] == "low"
    assert graph._get_provider_kwargs()["thinking_level"] == "low"


@pytest.mark.parametrize("provider", ["anthropic", "google", "openai", "azure", "bedrock"])
def test_other_provider_common_reasoning_is_not_fallback(harness, provider):
    harness.config.update(deep_think_llm_provider=provider, quick_think_llm_provider="google")
    if provider == "google":
        harness.config["openai_reasoning_effort"] = "high"
    else:
        harness.config["google_thinking_level"] = "minimal"
    construct(harness)
    assert {"reasoning_effort", "thinking_level", "effort"}.isdisjoint(harness.calls[0])
