"""Tests for env-driven CLI behavior (#897, #873).

The config-layer override (TRADINGAGENTS_* -> DEFAULT_CONFIG) is covered by
test_env_overrides.py. These tests cover the CLI layer: an env-configured
provider/model/language must skip its interactive prompt and use the value.
"""

import importlib
import os
import socket
import unittest
from copy import deepcopy
from unittest import mock

import pytest

import cli.selections as cli_selections


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access is forbidden in CLI configuration tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


REASONING_PROVIDERS = [
    ("openai", "openai_reasoning_effort", "reasoning_effort", "ask_openai_reasoning_effort"),
    ("anthropic", "anthropic_effort", "effort", "ask_anthropic_effort"),
    ("google", "google_thinking_level", "thinking_level", "ask_gemini_thinking_config"),
]
MISSING = object()


@pytest.fixture
def cli_client_run(monkeypatch, tmp_path):
    """Exercise real selection, assembly and resolution; stop before analysis."""
    import cli.run as cli_run
    import cli.selections as cli_selections
    import tradingagents.default_config as dc
    from cli.models import AnalystType
    from tradingagents.graph import trading_graph as graph_module

    # Like test_env_overrides: reload under clean env, restore the original
    # DEFAULT_CONFIG object afterward, including existing imported references.
    monkeypatch.setattr(dc, "DEFAULT_CONFIG", dc.DEFAULT_CONFIG)
    for key in dc._ENV_OVERRIDES:
        monkeypatch.delenv(key, raising=False)

    def run(provider, env, config_overrides=None):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        importlib.reload(dc)
        defaults = deepcopy(dc.DEFAULT_CONFIG)
        defaults.update(data_cache_dir=str(tmp_path / "cache"),
                        results_dir=str(tmp_path / "results"))
        for key, value in (config_overrides or {}).items():
            if value is MISSING:
                defaults.pop(key, None)
            else:
                defaults[key] = value
        monkeypatch.setattr(cli_selections, "DEFAULT_CONFIG", defaults)
        monkeypatch.setattr(cli_run, "DEFAULT_CONFIG", defaults)
        before_defaults = deepcopy(defaults)

        prompts = {}
        for name, value in {
            "fetch_announcements": None, "display_announcements": None,
            "get_ticker": "AAPL", "get_analysis_date": "2026-05-29",
            "select_analysts": [AnalystType.MARKET], "select_research_depth": 2,
            "ensure_api_key": None, "select_llm_provider": (provider, None),
            "ask_output_language": "English",
            "select_shallow_thinking_agent": defaults["quick_think_llm"],
            "select_deep_thinking_agent": defaults["deep_think_llm"],
            "ask_openai_reasoning_effort": "medium",
            "ask_anthropic_effort": "medium", "ask_gemini_thinking_config": "medium",
        }.items():
            prompts[name] = mock.MagicMock(return_value=value)
            monkeypatch.setattr(cli_selections, name, prompts[name])

        factory = mock.MagicMock(side_effect=[mock.MagicMock(), mock.MagicMock()])
        monkeypatch.setattr(graph_module, "create_llm_client", factory)
        for name in ("set_config", "TradingMemoryLog", "ConditionalLogic", "GraphSetup",
                     "Propagator", "Reflector"):
            monkeypatch.setattr(graph_module, name, mock.MagicMock())
        captured = {}

        class ClientsCreated(Exception):
            pass

        def construct(*args, **kwargs):
            factory.assert_not_called()
            captured["config"] = deepcopy(kwargs["config"])
            graph_module.TradingAgentsGraph(*args, **kwargs)
            assert kwargs["config"] == captured["config"]
            raise ClientsCreated

        monkeypatch.setattr(cli_run, "TradingAgentsGraph", construct)
        with pytest.raises(ClientsCreated):
            cli_run.run_analysis(checkpoint=None)

        assert defaults == before_defaults
        assert factory.call_count == 2
        config = captured["config"]
        assert config["llm_provider"] == provider
        assert config["max_debate_rounds"] == config["max_risk_discuss_rounds"] == 2
        assert config["output_language"] == "English"
        assert config["checkpoint_enabled"] == defaults["checkpoint_enabled"]
        for call, tier in zip(factory.call_args_list, ("deep", "quick"), strict=True):
            assert call.kwargs["model"] == defaults[f"{tier}_think_llm"]
            assert call.kwargs["provider"] == provider
            assert call.kwargs["base_url"] == config["backend_url"]
            assert call.kwargs["callbacks"]
        return config, factory.call_args_list, prompts

    return run


@pytest.mark.unit
@pytest.mark.parametrize("provider,shared_key,parameter,prompt", REASONING_PROVIDERS)
@pytest.mark.parametrize("scenario", ["deep-only", "provider-env", "tiers-only", "shared-env"])
def test_tier_env_survives_cli_to_clients(
    cli_client_run, provider, shared_key, parameter, prompt, scenario,
):
    env = {"TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT": "high"}
    if scenario in ("provider-env", "tiers-only"):
        env["TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT"] = "low"
    if scenario == "provider-env":
        env["TRADINGAGENTS_LLM_PROVIDER"] = provider
    if scenario == "shared-env":
        env[f"TRADINGAGENTS_{shared_key.upper()}"] = "medium"

    config, calls, prompts = cli_client_run(provider, env)
    assert config["deep_think_reasoning_effort"] == "high"
    expected_quick = "low" if scenario in ("provider-env", "tiers-only") else "medium"
    assert config["quick_think_reasoning_effort"] == (
        "low" if scenario in ("provider-env", "tiers-only") else None
    )
    assert config[shared_key] == (None if scenario == "provider-env" else "medium")
    assert calls[0].kwargs[parameter] == "high"
    assert calls[1].kwargs[parameter] == expected_quick
    for _, _, _, name in REASONING_PROVIDERS:
        if name == prompt and scenario in ("deep-only", "tiers-only"):
            prompts[name].assert_called_once_with()
        else:
            prompts[name].assert_not_called()
    if scenario == "provider-env":
        prompts["select_llm_provider"].assert_not_called()
    else:
        prompts["select_llm_provider"].assert_called_once_with(mock.ANY)


@pytest.mark.unit
@pytest.mark.parametrize("provider,shared_key,parameter,prompt", REASONING_PROVIDERS)
@pytest.mark.parametrize("value", [MISSING, None, "", "none"],
                         ids=["missing", "null", "empty", "literal-none"])
def test_cli_shared_choice_is_fallback_only(
    cli_client_run, provider, shared_key, parameter, prompt, value,
):
    config, calls, prompts = cli_client_run(provider, {}, {
        "deep_think_reasoning_effort": value,
        "quick_think_reasoning_effort": value,
    })
    for tier in ("deep", "quick"):
        key = f"{tier}_think_reasoning_effort"
        if value is MISSING:
            assert key not in config
        else:
            assert config[key] == value
    assert config[shared_key] == "medium"
    expected = "none" if value == "none" else "medium"
    assert [call.kwargs[parameter] for call in calls] == [expected, expected]
    prompts[prompt].assert_called_once_with()


@pytest.mark.unit
class TestProviderDefaultUrl(unittest.TestCase):
    def test_known_providers_resolve(self):
        from cli.prompts import provider_default_url
        self.assertEqual(provider_default_url("openai"), "https://api.openai.com/v1")
        self.assertEqual(provider_default_url("DeepSeek"), "https://api.deepseek.com")
        self.assertIsNone(provider_default_url("google"))  # uses SDK default

    def test_unknown_provider_returns_none(self):
        from cli.prompts import provider_default_url
        self.assertIsNone(provider_default_url("not-a-provider"))

    def test_ollama_honors_base_url_env(self):
        from cli.prompts import provider_default_url
        with mock.patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://host:1234/v1"}):
            self.assertEqual(provider_default_url("ollama"), "http://host:1234/v1")


@pytest.mark.unit
class TestCliSkipsPromptsFromEnv(unittest.TestCase):
    def test_env_config_skips_llm_prompts(self):

        env = {
            "TRADINGAGENTS_LLM_PROVIDER": "openai",
            "TRADINGAGENTS_DEEP_THINK_LLM": "kimi-k2.5",
            "TRADINGAGENTS_QUICK_THINK_LLM": "deepseek-v4-pro",
            "TRADINGAGENTS_LLM_BACKEND_URL": "https://opencode.ai/zen/go/v1",
            "TRADINGAGENTS_OUTPUT_LANGUAGE": "Japanese",
        }
        fake_cfg = dict(cli_selections.DEFAULT_CONFIG)
        fake_cfg.update({
            "llm_provider": "openai",
            "backend_url": "https://opencode.ai/zen/go/v1",
            "quick_think_llm": "deepseek-v4-pro",
            "deep_think_llm": "kimi-k2.5",
            "output_language": "Japanese",
        })

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(cli_selections, "DEFAULT_CONFIG", fake_cfg), \
             mock.patch.object(cli_selections, "fetch_announcements", return_value=None), \
             mock.patch.object(cli_selections, "display_announcements"), \
             mock.patch.object(cli_selections, "get_ticker", return_value="AAPL"), \
             mock.patch.object(cli_selections, "get_analysis_date", return_value="2026-05-29"), \
             mock.patch.object(cli_selections, "select_analysts", return_value=[]), \
             mock.patch.object(cli_selections, "select_research_depth", return_value=1), \
             mock.patch.object(cli_selections, "ensure_api_key") as ensure_key, \
             mock.patch.object(cli_selections, "select_llm_provider") as prompt_provider, \
             mock.patch.object(cli_selections, "ask_output_language") as prompt_lang, \
             mock.patch.object(cli_selections, "select_shallow_thinking_agent") as prompt_quick, \
             mock.patch.object(cli_selections, "select_deep_thinking_agent") as prompt_deep:
            sel = cli_selections.get_user_selections()

        # None of the LLM selection prompts should have been shown.
        prompt_provider.assert_not_called()
        prompt_lang.assert_not_called()
        prompt_quick.assert_not_called()
        prompt_deep.assert_not_called()
        # API key is still verified for the env-configured provider.
        ensure_key.assert_called_once()

        # The env values flow into the returned selections.
        self.assertEqual(sel["llm_provider"], "openai")
        self.assertEqual(sel["backend_url"], "https://opencode.ai/zen/go/v1")
        self.assertEqual(sel["quick_think_llm"], "deepseek-v4-pro")
        self.assertEqual(sel["deep_think_llm"], "kimi-k2.5")
        self.assertEqual(sel["output_language"], "Japanese")


@pytest.mark.unit
class TestResearchDepthSkippedFromEnv(unittest.TestCase):
    def test_both_round_envs_skip_depth_prompt(self):

        env = {
            "TRADINGAGENTS_MAX_DEBATE_ROUNDS": "2",
            "TRADINGAGENTS_MAX_RISK_ROUNDS": "4",
        }
        fake_cfg = dict(cli_selections.DEFAULT_CONFIG)
        fake_cfg.update({"max_debate_rounds": 2, "max_risk_discuss_rounds": 4})

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(cli_selections, "DEFAULT_CONFIG", fake_cfg), \
             mock.patch.object(cli_selections, "fetch_announcements", return_value=None), \
             mock.patch.object(cli_selections, "display_announcements"), \
             mock.patch.object(cli_selections, "get_ticker", return_value="AAPL"), \
             mock.patch.object(cli_selections, "get_analysis_date", return_value="2026-05-29"), \
             mock.patch.object(cli_selections, "select_analysts", return_value=[]), \
             mock.patch.object(cli_selections, "select_research_depth") as prompt_depth, \
             mock.patch.object(cli_selections, "ensure_api_key"), \
             mock.patch.object(cli_selections, "select_llm_provider", return_value=("openai", None)), \
             mock.patch.object(cli_selections, "ask_output_language", return_value="English"), \
             mock.patch.object(cli_selections, "select_shallow_thinking_agent", return_value="gpt-5.4-mini"), \
             mock.patch.object(cli_selections, "select_deep_thinking_agent", return_value="gpt-5.5"), \
             mock.patch.object(cli_selections, "ask_openai_reasoning_effort", return_value=None):
            sel = cli_selections.get_user_selections()

        # The research-depth prompt is skipped; the value comes from the env config.
        prompt_depth.assert_not_called()
        self.assertEqual(sel["research_depth"], 2)


@pytest.mark.unit
class TestReasoningEffortSkippedFromEnv(unittest.TestCase):
    def test_effort_env_skips_step8_prompt(self):

        env = {"TRADINGAGENTS_OPENAI_REASONING_EFFORT": "high"}
        fake_cfg = dict(cli_selections.DEFAULT_CONFIG)
        fake_cfg.update({"openai_reasoning_effort": "high"})

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(cli_selections, "DEFAULT_CONFIG", fake_cfg), \
             mock.patch.object(cli_selections, "fetch_announcements", return_value=None), \
             mock.patch.object(cli_selections, "display_announcements"), \
             mock.patch.object(cli_selections, "get_ticker", return_value="AAPL"), \
             mock.patch.object(cli_selections, "get_analysis_date", return_value="2026-05-29"), \
             mock.patch.object(cli_selections, "select_analysts", return_value=[]), \
             mock.patch.object(cli_selections, "select_research_depth", return_value=1), \
             mock.patch.object(cli_selections, "ensure_api_key"), \
             mock.patch.object(cli_selections, "select_llm_provider", return_value=("openai", None)), \
             mock.patch.object(cli_selections, "ask_output_language", return_value="English"), \
             mock.patch.object(cli_selections, "select_shallow_thinking_agent", return_value="gpt-5.4-mini"), \
             mock.patch.object(cli_selections, "select_deep_thinking_agent", return_value="gpt-5.5"), \
             mock.patch.object(cli_selections, "ask_openai_reasoning_effort") as prompt_effort:
            sel = cli_selections.get_user_selections()

        # The reasoning-effort prompt is skipped; the value comes from env config.
        prompt_effort.assert_not_called()
        self.assertEqual(sel["openai_reasoning_effort"], "high")


if __name__ == "__main__":
    unittest.main()
