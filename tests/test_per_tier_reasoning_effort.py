"""Per-tier resolution and real constructor wiring, with no external services."""

import socket
from copy import deepcopy
from itertools import product
from unittest.mock import MagicMock

import pytest

from tradingagents.graph import trading_graph as graph_module

pytestmark = pytest.mark.unit
MISSING = object()
PROVIDERS = [
    ("openai", "openai_reasoning_effort", "reasoning_effort"),
    ("anthropic", "anthropic_effort", "effort"),
    ("google", "google_thinking_level", "thinking_level"),
    ("deepseek", "openai_reasoning_effort", "reasoning_effort"),
    ("azure", "openai_reasoning_effort", "reasoning_effort"),
    ("bedrock", None, None),
]
CASES = [
    pytest.param("high", "low", "medium", "high", "low", id="distinct"),
    pytest.param("high", MISSING, "medium", "high", "medium", id="deep-only"),
    pytest.param(MISSING, "low", MISSING, None, "low", id="quick-only"),
    pytest.param(MISSING, MISSING, "medium", "medium", "medium", id="old-config"),
    pytest.param("none", " custom-level ", "medium", "none", " custom-level ", id="verbatim"),
    pytest.param(MISSING, MISSING, "none", "none", "none", id="common-none-literal"),
    pytest.param("minimal", "high", MISSING, "minimal", "high", id="adapter-owned"),
]
CASES += [
    pytest.param(
        deep, quick, common,
        None if common is MISSING else (common or None),
        None if common is MISSING else (common or None),
        id=f"inherit-{i}",
    )
    for i, (deep, quick, common) in enumerate(
        product((MISSING, None, ""), (MISSING, None, ""), (MISSING, None, "", "medium"))
    )
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access is forbidden in per-tier reasoning tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def reasoning_config(provider, common_key, deep, quick, common):
    # Inactive provider values must never become fallback for the active one.
    config = {key: "inactive" for _, key, _ in PROVIDERS if key and key != common_key}
    config["llm_provider"] = provider
    for key, value in (
        ("deep_think_reasoning_effort", deep),
        ("quick_think_reasoning_effort", quick),
        (common_key, common),
    ):
        if key and value is not MISSING:
            config[key] = value
    return config


def expected_reasoning(parameter, value):
    return {parameter: value} if parameter and value else {}


@pytest.mark.parametrize("provider,common_key,parameter", PROVIDERS)
@pytest.mark.parametrize("deep,quick,common,expected_deep,expected_quick", CASES)
def test_resolution_and_no_tier_compatibility(
    provider, common_key, parameter, deep, quick, common, expected_deep, expected_quick,
):
    config = reasoning_config(provider, common_key, deep, quick, common)
    before = deepcopy(config)
    graph = object.__new__(graph_module.TradingAgentsGraph)
    graph.config = config
    deep_kwargs = graph._get_provider_kwargs("deep")
    quick_kwargs = graph._get_provider_kwargs("quick")
    assert deep_kwargs == expected_reasoning(parameter, expected_deep)
    assert quick_kwargs == expected_reasoning(parameter, expected_quick)
    assert deep_kwargs is not quick_kwargs
    common_value = None if common is MISSING else common
    assert graph._get_provider_kwargs() == expected_reasoning(parameter, common_value)
    assert config == before
    assert graph.config is config


@pytest.mark.parametrize("provider,common_key,parameter", PROVIDERS)
@pytest.mark.parametrize("deep,quick,common,expected_deep,expected_quick", CASES)
@pytest.mark.parametrize("with_callbacks", [False, True])
def test_both_factory_calls(
    monkeypatch, tmp_path, provider, common_key, parameter,
    deep, quick, common, expected_deep, expected_quick, with_callbacks,
):
    config = reasoning_config(provider, common_key, deep, quick, common)
    config.update(
        deep_think_llm="custom-deep-model", quick_think_llm="custom-quick-model",
        backend_url="https://gateway.invalid/v1", temperature="0.2",
        llm_max_retries="3", max_tokens="4096", max_debate_rounds=1,
        max_risk_discuss_rounds=1, data_cache_dir=str(tmp_path / "cache"),
        results_dir=str(tmp_path / "results"),
    )
    before = deepcopy(config)
    clients = [MagicMock(), MagicMock()]
    factory = MagicMock(side_effect=clients)
    monkeypatch.setattr(graph_module, "create_llm_client", factory)
    components = {}
    for name in ("set_config", "TradingMemoryLog", "ConditionalLogic", "GraphSetup",
                 "Propagator", "Reflector"):
        components[name] = MagicMock()
        monkeypatch.setattr(graph_module, name, components[name])
    callbacks = [object()] if with_callbacks else []
    graph = graph_module.TradingAgentsGraph(config=config, callbacks=callbacks)

    assert factory.call_count == 2
    calls = factory.call_args_list
    assert calls[0].kwargs is not calls[1].kwargs
    for call, model, effort in zip(
        calls, ("custom-deep-model", "custom-quick-model"), (expected_deep, expected_quick),
        strict=True,
    ):
        expected = {
            "provider": provider, "model": model, "base_url": config["backend_url"],
            "temperature": 0.2, "max_retries": 3,
        }
        expected["max_output_tokens" if provider == "google" else "max_tokens"] = 4096
        expected.update(expected_reasoning(parameter, effort))
        if with_callbacks:
            expected["callbacks"] = callbacks
            assert call.kwargs["callbacks"] is callbacks
        assert call.args == ()
        assert call.kwargs == expected
    assert graph.deep_thinking_llm is clients[0].get_llm.return_value
    assert graph.quick_thinking_llm is clients[1].get_llm.return_value
    for client in clients:
        client.get_llm.assert_called_once_with()
    components["GraphSetup"].assert_called_once_with(
        graph.quick_thinking_llm, graph.deep_thinking_llm, graph.conditional_logic
    )
    components["Reflector"].assert_called_once_with(graph.quick_thinking_llm)
    assert graph.config is config
    assert graph.config == before
    assert config == before
