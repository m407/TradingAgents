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
def _isolated_cli(monkeypatch, tmp_path):
    """Never consume user config/credentials or persist a prompted credential."""
    import dotenv

    def forbidden(*args, **kwargs):
        pytest.fail("Credential prompts/writes are forbidden in CLI configuration tests")

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    with mock.patch.dict(os.environ, {
        "PYTHON_DOTENV_DISABLED": "1", "HOME": str(tmp_path),
    }, clear=True):
        import questionary

        import cli.prompts as prompts
        import cli.run as cli_run
        import cli.selections as cli_selections
        import tradingagents.default_config as dc

        monkeypatch.setattr(dc, "DEFAULT_CONFIG", dc.DEFAULT_CONFIG)
        importlib.reload(dc)
        monkeypatch.setattr(cli_selections, "DEFAULT_CONFIG", deepcopy(dc.DEFAULT_CONFIG))
        monkeypatch.setattr(cli_run, "DEFAULT_CONFIG", deepcopy(dc.DEFAULT_CONFIG))
        monkeypatch.setattr(questionary, "password", forbidden)
        monkeypatch.setattr(prompts, "set_key", forbidden)
        monkeypatch.setattr(prompts, "find_dotenv", forbidden)
        yield


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

    def run(provider, env, config_overrides=None, *, menu_url=None,
            prompt_values=None, real_auth=False, client_factory=None, error=None,
            error_match=None):
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
            "ensure_api_key": None, "select_llm_provider": (provider, menu_url),
            "prompt_openai_compatible_url": "http://generic.test/v1",
            "ask_output_language": "English",
            "select_shallow_thinking_agent": defaults["quick_think_llm"],
            "select_deep_thinking_agent": defaults["deep_think_llm"],
            "ask_openai_reasoning_effort": "medium",
            "ask_anthropic_effort": "medium", "ask_gemini_thinking_config": "medium",
        }.items():
            prompts[name] = mock.MagicMock(return_value=value)
            monkeypatch.setattr(cli_selections, name, prompts[name])
        for name, value in (prompt_values or {}).items():
            prompts[name].return_value = value
        if real_auth:
            from cli.prompts import ensure_api_key
            prompts["ensure_api_key"].side_effect = ensure_api_key

        factory = mock.MagicMock(side_effect=client_factory or [mock.MagicMock(), mock.MagicMock()])
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
            try:
                graph_module.TradingAgentsGraph(*args, **kwargs)
            finally:
                assert kwargs["config"] == captured["config"]
            raise ClientsCreated

        monkeypatch.setattr(cli_run, "TradingAgentsGraph", construct)
        with pytest.raises(error or ClientsCreated, match=error_match):
            cli_run.run_analysis(checkpoint=None)

        assert defaults == before_defaults
        if error:
            return captured["config"], factory.call_args_list, prompts
        assert factory.call_count == 2
        config = captured["config"]
        assert config["llm_provider"] == provider
        assert config["max_debate_rounds"] == config["max_risk_discuss_rounds"] == 2
        assert config["output_language"] == "English"
        assert config["checkpoint_enabled"] == defaults["checkpoint_enabled"]
        for call, tier in zip(factory.call_args_list, ("deep", "quick"), strict=True):
            assert call.kwargs["model"] == defaults[f"{tier}_think_llm"]
            assert call.kwargs["provider"] == (
                defaults.get(f"{tier}_think_llm_provider") or provider
            ).lower()
            assert call.kwargs["base_url"] == (
                defaults.get(f"{tier}_think_llm_backend_url") or config["backend_url"] or None
            )
            assert call.kwargs["callbacks"]
        assert factory.call_args_list[0].kwargs["callbacks"] is factory.call_args_list[1].kwargs["callbacks"]
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


@pytest.mark.parametrize("providers", [("OpenAI", "openai"), ("openai", "Google"), ("google", "openai")])
def test_env_connections_reach_both_clients(cli_client_run, providers):
    env = {
        "TRADINGAGENTS_LLM_PROVIDER": "anthropic",  # unused: no auth check
        "TRADINGAGENTS_LLM_BACKEND_URL": "http://common.test/v1",
        "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "none",
        "TRADINGAGENTS_GOOGLE_THINKING_LEVEL": "minimal",
        "TRADINGAGENTS_TEMPERATURE": "0.2",
        "TRADINGAGENTS_LLM_MAX_RETRIES": "3",
        "TRADINGAGENTS_MAX_TOKENS": "1024",
    }
    for tier, provider in zip(("DEEP", "QUICK"), providers):
        env[f"TRADINGAGENTS_{tier}_THINK_LLM_PROVIDER"] = provider
        env[f"TRADINGAGENTS_{tier}_THINK_LLM_BACKEND_URL"] = f"http://{tier.lower()}.test/v1"
        env[f"TRADINGAGENTS_{tier}_THINK_LLM"] = f"direct-{tier.lower()}-pro-model"
    config, calls, prompts = cli_client_run("anthropic", env)
    for tier, call in zip(("deep", "quick"), calls):
        assert config[f"{tier}_think_llm_provider"] == env[f"TRADINGAGENTS_{tier.upper()}_THINK_LLM_PROVIDER"]
        assert config[f"{tier}_think_llm_backend_url"] == f"http://{tier}.test/v1"
        kwargs = call.kwargs
        google = kwargs["provider"] == "google"
        assert kwargs == {
            "provider": env[f"TRADINGAGENTS_{tier.upper()}_THINK_LLM_PROVIDER"].lower(),
            "model": f"direct-{tier}-pro-model", "base_url": f"http://{tier}.test/v1",
            "thinking_level" if google else "reasoning_effort": "minimal" if google else "none",
            "max_output_tokens" if google else "max_tokens": 1024,
            "temperature": 0.2, "max_retries": 3, "callbacks": kwargs["callbacks"],
        }
    assert prompts["ensure_api_key"].call_args_list == [
        mock.call(p) for p in dict.fromkeys(p.lower() for p in providers)
    ]
    for name in ("select_llm_provider", "select_shallow_thinking_agent", "select_deep_thinking_agent",
                 "prompt_openai_compatible_url", *(p[3] for p in REASONING_PROVIDERS)):
        prompts[name].assert_not_called()


@pytest.mark.parametrize("tier", ["DEEP", "QUICK"])
def test_one_env_model_still_skips_both_menus(cli_client_run, tier):
    config, calls, prompts = cli_client_run("openai", {
        f"TRADINGAGENTS_{tier}_THINK_LLM": "direct-model-id",
        "TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": "google",
    })
    prompts["select_llm_provider"].assert_called_once_with(mock.ANY)
    prompts["select_shallow_thinking_agent"].assert_not_called()
    prompts["select_deep_thinking_agent"].assert_not_called()
    assert config[f"{tier.lower()}_think_llm"] == "direct-model-id"
    assert calls[0 if tier == "DEEP" else 1].kwargs["model"] == "direct-model-id"
    other = "quick" if tier == "DEEP" else "deep"
    assert config[f"{other}_think_llm"] == ("gpt-6-luna" if other == "quick" else "gpt-6-sol")


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
@pytest.mark.parametrize("with_key", [False, True])
def test_optional_auth_uses_existing_rules(cli_client_run, monkeypatch, provider, with_key):
    if with_key:
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "test-only-key")
    _, _, prompts = cli_client_run("anthropic", {
        "TRADINGAGENTS_LLM_PROVIDER": "anthropic",
        "TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER": provider,
        "TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": provider,
        "TRADINGAGENTS_LLM_BACKEND_URL": "http://local.test/v1",
    }, real_auth=True)
    prompts["ensure_api_key"].assert_called_once_with(provider)


@pytest.mark.parametrize("provider,key", [("openai", "OPENAI_API_KEY"), ("google", "GOOGLE_API_KEY"),
                                          ("anthropic", "ANTHROPIC_API_KEY")])
def test_actual_provider_auth_mapping(cli_client_run, monkeypatch, provider, key):
    monkeypatch.setenv(key, "test-only-key")
    _, _, prompts = cli_client_run("openai_compatible", {
        "TRADINGAGENTS_LLM_PROVIDER": "openai_compatible",
        "TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER": provider,
        "TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": provider,
    }, real_auth=True)
    prompts["ensure_api_key"].assert_called_once_with(provider)


@pytest.mark.parametrize("provider_env", [False, True])
@pytest.mark.parametrize("providers", [("openai", "google"), ("openai_compatible", "openai_compatible")])
def test_unused_common_generic_url_never_prompts(cli_client_run, provider_env, providers):
    env = {"TRADINGAGENTS_LLM_PROVIDER": "openai_compatible"} if provider_env else {}
    for tier, provider in zip(("DEEP", "QUICK"), providers):
        env[f"TRADINGAGENTS_{tier}_THINK_LLM_PROVIDER"] = provider
        env[f"TRADINGAGENTS_{tier}_THINK_LLM_BACKEND_URL"] = f"http://{tier.lower()}.test/v1"
    config, _, prompts = cli_client_run("openai_compatible", env)
    assert config["backend_url"] is None
    prompts["prompt_openai_compatible_url"].assert_not_called()
    assert prompts["select_llm_provider"].call_count == (0 if provider_env else 1)


def test_used_common_generic_url_keeps_existing_prompt(cli_client_run):
    config, calls, prompts = cli_client_run("openai_compatible", {})
    prompts["prompt_openai_compatible_url"].assert_called_once_with(mock.ANY)
    assert config["backend_url"] == "http://generic.test/v1"
    assert all(call.kwargs["base_url"] == config["backend_url"] for call in calls)


@pytest.mark.parametrize("common", ["google", "openai_compatible"])
def test_actual_generic_missing_url_keeps_client_error(cli_client_run, common):
    from tradingagents.llm_clients import create_llm_client

    # The real generic client fails before constructing an SDK client.
    _, calls, prompts = cli_client_run(common, {
        "TRADINGAGENTS_LLM_PROVIDER": common,
        "TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER": "openai_compatible",
    }, client_factory=create_llm_client, error=ValueError,
        error_match="Provider 'openai_compatible' requires a base_url")
    assert calls[0].kwargs["base_url"] is None
    prompts["prompt_openai_compatible_url"].assert_not_called()


@pytest.mark.parametrize("providers", [("openai", "google"), ("google", "openai")])
def test_interactive_unused_generic_without_any_urls(cli_client_run, providers):
    config, calls, prompts = cli_client_run("openai_compatible", {
        "TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER": providers[0],
        "TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": providers[1],
    })
    assert config["backend_url"] is None
    assert all(c.kwargs["base_url"] is None for c in calls)
    prompts["prompt_openai_compatible_url"].assert_not_called()
    prompts["select_llm_provider"].assert_called_once_with(mock.ANY)
    prompts["select_deep_thinking_agent"].assert_called_once_with(providers[0], mock.ANY)
    prompts["select_shallow_thinking_agent"].assert_called_once_with(providers[1], mock.ANY)
    assert prompts["ensure_api_key"].call_args_list == [mock.call(p) for p in providers]


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
