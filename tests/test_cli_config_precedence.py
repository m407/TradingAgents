"""CLI config precedence (#976, #977).

An explicit environment override for the debate/risk round counts, or the
checkpoint flag, must win over the interactive research-depth selection — the CLI
must not clobber an env-configured value back to a prompt/flag default.
"""

from copy import deepcopy
from unittest import mock

import pytest

import cli.run as cli_run
from tests.test_cli_env_skip import (  # shared offline ENV -> CLI -> client harness
    MISSING,
    REASONING_PROVIDERS,
    _isolated_cli as _isolated_cli,
    _no_network as _no_network,
    cli_client_run as cli_client_run,
)

# Minimal selections dict shaped like get_user_selections()'s return value.
SELECTIONS = {
    "research_depth": 5,
    "quick_think_llm": "gpt-5.4-mini",
    "deep_think_llm": "gpt-5.5",
    "backend_url": None,
    "llm_provider": "openai",
    "google_thinking_level": None,
    "openai_reasoning_effort": None,
    "anthropic_effort": None,
    "output_language": "English",
}


def test_research_depth_sets_both_rounds_without_env(monkeypatch):
    for var in ("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "TRADINGAGENTS_MAX_RISK_ROUNDS"):
        monkeypatch.delenv(var, raising=False)
    cfg = cli_run._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 5
    assert cfg["max_risk_discuss_rounds"] == 5


def test_env_round_counts_win_over_selection(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "2")
    monkeypatch.setenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "4")
    # DEFAULT_CONFIG already reflects the env (applied at import); emulate that.
    patched = dict(cli_run.DEFAULT_CONFIG, max_debate_rounds=2, max_risk_discuss_rounds=4)
    with mock.patch.object(cli_run, "DEFAULT_CONFIG", patched):
        cfg = cli_run._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 2  # env value, not research_depth=5
    assert cfg["max_risk_discuss_rounds"] == 4


def test_partial_env_only_overrides_that_count(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "2")
    monkeypatch.delenv("TRADINGAGENTS_MAX_RISK_ROUNDS", raising=False)
    patched = dict(cli_run.DEFAULT_CONFIG, max_debate_rounds=2)
    with mock.patch.object(cli_run, "DEFAULT_CONFIG", patched):
        cfg = cli_run._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 2  # env wins
    assert cfg["max_risk_discuss_rounds"] == 5  # falls through to research_depth


def test_checkpoint_none_preserves_env_default():
    patched = dict(cli_run.DEFAULT_CONFIG, checkpoint_enabled=True)  # e.g. env-enabled
    with mock.patch.object(cli_run, "DEFAULT_CONFIG", patched):
        cfg = cli_run._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["checkpoint_enabled"] is True  # not clobbered back to False


@pytest.mark.parametrize("flag", [True, False])
def test_checkpoint_flag_overrides_env(flag):
    patched = dict(cli_run.DEFAULT_CONFIG, checkpoint_enabled=not flag)
    with mock.patch.object(cli_run, "DEFAULT_CONFIG", patched):
        cfg = cli_run._build_run_config(SELECTIONS, checkpoint=flag)
    assert cfg["checkpoint_enabled"] is flag


@pytest.mark.unit
def test_glm_resolves_to_the_endpoint_its_key_belongs_to():
    """The provider table, the client registry and the key mapping must name the
    same platform: glm is Z.AI international (ZHIPU_API_KEY) and glm-cn is
    BigModel China. A mismatch sends the key to the other platform and every
    call fails auth."""
    from cli.prompts import resolve_backend_url
    from tradingagents.llm_clients.api_key_env import get_api_key_env
    from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS

    assert resolve_backend_url("glm", None, None) == OPENAI_COMPATIBLE_PROVIDERS["glm"].base_url
    assert get_api_key_env("glm") == "ZHIPU_API_KEY"
    assert "z.ai" in OPENAI_COMPATIBLE_PROVIDERS["glm"].base_url
    assert "bigmodel.cn" in OPENAI_COMPATIBLE_PROVIDERS["glm-cn"].base_url


@pytest.mark.unit
def test_a_half_set_round_count_says_which_value_won(capsys, monkeypatch):
    """With only one of the two round-count variables set, the depth prompt is
    still shown but half the answer is discarded; the user was never told."""

    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1")
    monkeypatch.delenv("TRADINGAGENTS_MAX_RISK_ROUNDS", raising=False)
    printed = []
    monkeypatch.setattr(cli_run.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else ""))

    config = cli_run._build_run_config({
        "ticker": "NVDA", "analysis_date": "2026-09-01", "asset_type": "stock",
        "analysts": [], "research_depth": 5, "llm_provider": "openai",
        "quick_think_llm": "gpt-6-luna", "deep_think_llm": "gpt-6-sol",
        "backend_url": None, "output_language": "English",
    }, None)

    assert config["max_risk_discuss_rounds"] == 5
    assert any("TRADINGAGENTS_MAX_DEBATE_ROUNDS" in line for line in printed), printed


@pytest.mark.parametrize("common,other", [(0, 2), (2, 1), (1, 0)])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("override", [None, "none", "minimal"])
def test_interactive_reasoning_preserves_other_provider(
    cli_client_run, common, other, reverse, override,
):
    provider, shared, parameter, prompt = REASONING_PROVIDERS[common]
    other_provider, other_shared, other_parameter, _ = REASONING_PROVIDERS[other]
    providers = [provider, other_provider]
    if reverse:
        providers.reverse()
    env = {
        "TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER": providers[0],
        "TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": providers[1],
        f"TRADINGAGENTS_{other_shared.upper()}": "minimal" if other_provider == "google" else "high",
    }
    if override:
        env["TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT"] = override
    config, calls, prompts = cli_client_run(provider, env)
    assert config[shared] == "medium"
    assert config[other_shared] == env[f"TRADINGAGENTS_{other_shared.upper()}"]
    for index, call in enumerate(calls):
        selected_common = providers[index] == provider
        key = parameter if selected_common else other_parameter
        expected = "medium" if selected_common else config[other_shared]
        assert call.kwargs[key] == (override if index == 0 and override else expected)
        assert set(call.kwargs) & {"effort", "thinking_level", "reasoning_effort"} == {key}
    prompts[prompt].assert_called_once_with()
    for _, _, _, name in REASONING_PROVIDERS:
        if name != prompt:
            prompts[name].assert_not_called()
    prompts["select_llm_provider"].assert_called_once_with(mock.ANY)
    prompts["select_deep_thinking_agent"].assert_called_once_with(providers[0], mock.ANY)
    prompts["select_shallow_thinking_agent"].assert_called_once_with(providers[1], mock.ANY)
    assert prompts["ensure_api_key"].call_args_list == [mock.call(p) for p in dict.fromkeys(providers)]


@pytest.mark.parametrize("value", [MISSING, None, ""])
@pytest.mark.parametrize("url_source", ["env", "menu", "default", "absent"])
def test_connection_inheritance_is_independent(cli_client_run, value, url_source):
    common = "google" if url_source == "absent" else "openai"
    env = {"TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": "Google"}
    if url_source == "env":
        env["TRADINGAGENTS_LLM_BACKEND_URL"] = "http://env.test/common"
    menu_url = "http://menu.test/common" if url_source == "menu" else None
    config, calls, prompts = cli_client_run(common, env, {
        "deep_think_llm_provider": value,
        "deep_think_llm_backend_url": value,
        "quick_think_llm_backend_url": value,
    }, menu_url=menu_url)
    expected_url = {
        "env": "http://env.test/common", "menu": "http://menu.test/common",
        "default": "https://api.openai.com/v1", "absent": None,
    }[url_source]
    assert config["backend_url"] == expected_url
    assert [c.kwargs["base_url"] for c in calls] == [expected_url, expected_url]
    assert [c.kwargs["provider"] for c in calls] == [common, "google"]
    prompts["select_deep_thinking_agent"].assert_called_once_with(common, mock.ANY)
    prompts["select_shallow_thinking_agent"].assert_called_once_with("google", mock.ANY)


def test_env_url_without_provider_does_not_change_provider(cli_client_run):
    config, calls, prompts = cli_client_run("openai", {
        "TRADINGAGENTS_DEEP_THINK_LLM_BACKEND_URL": "http://deep.test/v1",
        "TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": "",
        "TRADINGAGENTS_QUICK_THINK_LLM_BACKEND_URL": "",
    })
    assert config["deep_think_llm_provider"] is None
    assert calls[0].kwargs["base_url"] == "http://deep.test/v1"
    assert calls[1].kwargs["base_url"] == "https://api.openai.com/v1"
    assert all(c.kwargs["provider"] == "openai" for c in calls)
    prompts["ensure_api_key"].assert_called_once_with("openai")


@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_absent_selections_never_erase_config(monkeypatch, value):
    defaults = deepcopy(cli_run.DEFAULT_CONFIG)
    protected = {
        "deep_think_llm_provider": "Google", "quick_think_llm_provider": "anthropic",
        "deep_think_llm_backend_url": "http://deep.test/v1beta",
        "quick_think_llm_backend_url": "http://quick.test/v1",
        "deep_think_reasoning_effort": "minimal", "quick_think_reasoning_effort": "none",
        "google_thinking_level": "minimal", "openai_reasoning_effort": "high",
        "anthropic_effort": "low",
    }
    defaults.update(protected)
    monkeypatch.setattr(cli_run, "DEFAULT_CONFIG", defaults)
    selections = dict(SELECTIONS)
    for key in protected:
        if value is MISSING:
            selections.pop(key, None)
        else:
            selections[key] = value
    before = deepcopy(defaults)
    config = cli_run._build_run_config(selections, None)
    assert defaults == before
    for key, expected in protected.items():
        assert config[key] == expected


@pytest.mark.parametrize("provider,shared,parameter,prompt", REASONING_PROVIDERS)
@pytest.mark.parametrize("selection", [None, ""])
def test_cancelled_common_reasoning_preserves_config(
    cli_client_run, provider, shared, parameter, prompt, selection,
):
    config, calls, prompts = cli_client_run(provider, {}, {shared: "high"},
                                            prompt_values={prompt: selection})
    assert config[shared] == "high"
    assert [c.kwargs[parameter] for c in calls] == ["high", "high"]
    prompts[prompt].assert_called_once_with()


@pytest.mark.parametrize("provider,parameter", [
    ("openai_compatible", "reasoning_effort"), ("deepseek", "reasoning_effort"),
    ("azure", "reasoning_effort"), ("anthropic", "effort"),
    ("google", "thinking_level"), ("bedrock", None),
])
@pytest.mark.parametrize("tier_value,common_value", [("none", "high"), ("", "minimal"), ("", "")])
def test_actual_provider_reasoning_precedence_to_clients(
    cli_client_run, provider, parameter, tier_value, common_value,
):
    shared = {"google": "GOOGLE_THINKING_LEVEL", "anthropic": "ANTHROPIC_EFFORT"}.get(
        provider, "OPENAI_REASONING_EFFORT",
    )
    _, calls, prompts = cli_client_run("openai", {
        "TRADINGAGENTS_LLM_PROVIDER": "openai",
        "TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER": provider,
        "TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER": provider,
        "TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT": tier_value,
        f"TRADINGAGENTS_{shared}": common_value,
        "TRADINGAGENTS_MAX_TOKENS": "2048",
        "TRADINGAGENTS_TEMPERATURE": "0.3",
        "TRADINGAGENTS_LLM_MAX_RETRIES": "4",
    })
    for call, value in zip(calls, (tier_value or common_value, common_value)):
        kwargs = call.kwargs
        reasoning = {key: kwargs[key] for key in ("reasoning_effort", "thinking_level", "effort") if key in kwargs}
        assert reasoning == ({parameter: value} if parameter and value else {})
        assert kwargs["max_output_tokens" if provider == "google" else "max_tokens"] == 2048
        assert (kwargs["temperature"], kwargs["max_retries"]) == (0.3, 4)
    prompts["ensure_api_key"].assert_called_once_with(provider)
    for _, _, _, prompt in REASONING_PROVIDERS:
        prompts[prompt].assert_not_called()
