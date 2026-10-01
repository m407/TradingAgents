"""Tests for TRADINGAGENTS_* env-var overlay onto DEFAULT_CONFIG."""

from __future__ import annotations

import importlib
import os

import pytest

import tradingagents.default_config as default_config_module


@pytest.fixture(autouse=True)
def _restore_default_config():
    """Do not leak the config created by a test's reload into other tests."""
    original = default_config_module.DEFAULT_CONFIG
    yield
    default_config_module.DEFAULT_CONFIG = original


def _reload_with_env(monkeypatch, **overrides):
    """Set/clear env vars then reload default_config to re-evaluate DEFAULT_CONFIG."""
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return importlib.reload(default_config_module)


def test_no_env_uses_built_in_defaults(monkeypatch):
    dc = _reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["llm_provider"] == "openai"
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == "gpt-6-sol"
    assert dc.DEFAULT_CONFIG["quick_think_llm"] == "gpt-6-luna"
    assert dc.DEFAULT_CONFIG["backend_url"] is None
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1
    assert dc.DEFAULT_CONFIG["checkpoint_enabled"] is False
    assert dc.DEFAULT_CONFIG["deep_think_llm_provider"] is None
    assert dc.DEFAULT_CONFIG["quick_think_llm_provider"] is None
    assert dc.DEFAULT_CONFIG["deep_think_llm_backend_url"] is None
    assert dc.DEFAULT_CONFIG["quick_think_llm_backend_url"] is None


_PER_MODEL_CONNECTION_OVERRIDES = [
    ("TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER", "deep_think_llm_provider", "openai"),
    ("TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER", "quick_think_llm_provider", "google"),
    ("TRADINGAGENTS_DEEP_THINK_LLM_BACKEND_URL", "deep_think_llm_backend_url",
     "https://deep.example.invalid/v1"),
    ("TRADINGAGENTS_QUICK_THINK_LLM_BACKEND_URL", "quick_think_llm_backend_url",
     "https://quick.example.invalid/v1beta"),
]


def test_per_model_connections_are_independent(monkeypatch):
    baseline = _reload_with_env(monkeypatch).DEFAULT_CONFIG.copy()
    overrides = {env: value for env, key, value in _PER_MODEL_CONNECTION_OVERRIDES}
    expected = baseline | {key: value for env, key, value in _PER_MODEL_CONNECTION_OVERRIDES}
    assert expected == _reload_with_env(monkeypatch, **overrides).DEFAULT_CONFIG


@pytest.mark.parametrize("env,key,value", _PER_MODEL_CONNECTION_OVERRIDES)
@pytest.mark.parametrize("raw", [None, "", "configured"])
@pytest.mark.parametrize("shared_overrides", [False, True])
def test_partial_per_model_connections(monkeypatch, env, key, value, raw, shared_overrides):
    overrides = {}
    if shared_overrides:
        overrides = {
            "TRADINGAGENTS_LLM_PROVIDER": "anthropic",
            "TRADINGAGENTS_LLM_BACKEND_URL": "https://shared.example.invalid",
            "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "high",
            "TRADINGAGENTS_GOOGLE_THINKING_LEVEL": "minimal",
            "TRADINGAGENTS_ANTHROPIC_EFFORT": "low",
            "TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT": "medium",
            "TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT": "high",
        }
    expected = _reload_with_env(monkeypatch, **overrides).DEFAULT_CONFIG.copy()
    if raw is not None:
        overrides[env] = value if raw else ""
    if raw:
        expected[key] = value
    # Full-dict equality also guards models, reasoning, shared connections and
    # every unrelated default. Unset/empty individual keys remain None.
    assert expected == _reload_with_env(monkeypatch, **overrides).DEFAULT_CONFIG


def test_string_overrides(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="google",
        TRADINGAGENTS_DEEP_THINK_LLM="gemini-3-pro-preview",
        TRADINGAGENTS_QUICK_THINK_LLM="gemini-3-flash-preview",
        TRADINGAGENTS_LLM_BACKEND_URL="https://example.invalid/v1",
        TRADINGAGENTS_OUTPUT_LANGUAGE="Chinese",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "google"
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == "gemini-3-pro-preview"
    assert dc.DEFAULT_CONFIG["quick_think_llm"] == "gemini-3-flash-preview"
    assert dc.DEFAULT_CONFIG["backend_url"] == "https://example.invalid/v1"
    assert dc.DEFAULT_CONFIG["output_language"] == "Chinese"


def test_news_env_overrides_and_mapping_validation(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_NEWS_VENDOR="combined",
        TRADINGAGENTS_FREEDOM_NEWS_LANGUAGE="en",
        TRADINGAGENTS_FREEDOM_SYMBOL_MAP='{"AAPL":"AAPL.US"}',
    )
    assert dc.DEFAULT_CONFIG["news_vendor"] == "combined"
    assert dc.DEFAULT_CONFIG["freedom_news_language"] == "en"
    assert dc.DEFAULT_CONFIG["freedom_symbol_map"] == {"AAPL": "AAPL.US"}
    assert dc.DEFAULT_CONFIG["freedom_news_page_size"] == 20
    assert dc.DEFAULT_CONFIG["freedom_news_max_pages"] == 3
    assert dc.DEFAULT_CONFIG["freedom_news_max_details"] == 40
    assert dc.DEFAULT_CONFIG["freedom_news_timeout_seconds"] == 15
    assert dc.DEFAULT_CONFIG["news_max_text_chars"] == 4000


def test_numeric_news_settings_are_environment_configurable(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_FREEDOM_NEWS_PAGE_SIZE="50",
        TRADINGAGENTS_FREEDOM_NEWS_MAX_PAGES="2",
        TRADINGAGENTS_FREEDOM_NEWS_MAX_DETAILS="12",
        TRADINGAGENTS_FREEDOM_NEWS_TIMEOUT_SECONDS="9",
        TRADINGAGENTS_NEWS_MAX_TEXT_CHARS="1200",
    )
    assert dc.DEFAULT_CONFIG["freedom_news_page_size"] == 50
    assert dc.DEFAULT_CONFIG["freedom_news_max_pages"] == 2
    assert dc.DEFAULT_CONFIG["freedom_news_max_details"] == 12
    assert dc.DEFAULT_CONFIG["freedom_news_timeout_seconds"] == 9
    assert dc.DEFAULT_CONFIG["news_max_text_chars"] == 1200


@pytest.mark.parametrize("raw", ["[]", '"AAPL.US"', '{"AAPL":1}', "not-json"])
def test_invalid_freedom_symbol_map_env_raises(monkeypatch, raw):
    monkeypatch.setenv("TRADINGAGENTS_FREEDOM_SYMBOL_MAP", raw)
    with pytest.raises(ValueError, match="TRADINGAGENTS_FREEDOM_SYMBOL_MAP"):
        importlib.reload(default_config_module)
    monkeypatch.delenv("TRADINGAGENTS_FREEDOM_SYMBOL_MAP", raising=False)
    importlib.reload(default_config_module)


def test_int_coercion(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="3",
        TRADINGAGENTS_MAX_RISK_ROUNDS="2",
    )
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 3
    assert isinstance(dc.DEFAULT_CONFIG["max_debate_rounds"], int)
    assert dc.DEFAULT_CONFIG["max_risk_discuss_rounds"] == 2
    assert isinstance(dc.DEFAULT_CONFIG["max_risk_discuss_rounds"], int)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ],
)
def test_bool_coercion(monkeypatch, raw, expected):
    dc = _reload_with_env(monkeypatch, TRADINGAGENTS_CHECKPOINT_ENABLED=raw)
    assert dc.DEFAULT_CONFIG["checkpoint_enabled"] is expected


def test_reasoning_thinking_overrides(monkeypatch):
    """The provider reasoning/thinking knobs are env-configurable (non-interactive runs)."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_OPENAI_REASONING_EFFORT="high",
        TRADINGAGENTS_GOOGLE_THINKING_LEVEL="minimal",
        TRADINGAGENTS_ANTHROPIC_EFFORT="low",
    )
    assert dc.DEFAULT_CONFIG["openai_reasoning_effort"] == "high"
    assert dc.DEFAULT_CONFIG["google_thinking_level"] == "minimal"
    assert dc.DEFAULT_CONFIG["anthropic_effort"] == "low"


def test_reasoning_effort_defaults_to_none(monkeypatch):
    """Unset reasoning/thinking knobs stay None so each provider uses its own default."""
    dc = _reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["openai_reasoning_effort"] is None
    assert dc.DEFAULT_CONFIG["google_thinking_level"] is None
    assert dc.DEFAULT_CONFIG["anthropic_effort"] is None
    assert dc.DEFAULT_CONFIG["deep_think_reasoning_effort"] is None
    assert dc.DEFAULT_CONFIG["quick_think_reasoning_effort"] is None


@pytest.mark.parametrize(
    "deep,quick",
    [
        ("high", "low"),
        ("low", "high"),
        ("high", None),
        (None, "low"),
        ("", ""),
        ("", "low"),
        ("high", ""),
        ("none", " custom-level "),
        (" custom-level ", "none"),
    ],
)
@pytest.mark.parametrize("override_models", [False, True])
def test_per_tier_reasoning_overrides(monkeypatch, deep, quick, override_models):
    overrides = {
        "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "medium",
        "TRADINGAGENTS_GOOGLE_THINKING_LEVEL": "minimal",
        "TRADINGAGENTS_ANTHROPIC_EFFORT": "high",
    }
    if override_models:
        overrides.update(
            TRADINGAGENTS_DEEP_THINK_LLM="custom-deep",
            TRADINGAGENTS_QUICK_THINK_LLM="custom-quick",
        )
    for tier, value in (("DEEP", deep), ("QUICK", quick)):
        if value is not None:
            overrides[f"TRADINGAGENTS_{tier}_THINK_REASONING_EFFORT"] = value

    dc = _reload_with_env(monkeypatch, **overrides)
    assert dc.DEFAULT_CONFIG["deep_think_reasoning_effort"] == (deep or None)
    assert dc.DEFAULT_CONFIG["quick_think_reasoning_effort"] == (quick or None)
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == (
        "custom-deep" if override_models else "gpt-6-sol"
    )
    assert dc.DEFAULT_CONFIG["quick_think_llm"] == (
        "custom-quick" if override_models else "gpt-6-luna"
    )
    assert dc.DEFAULT_CONFIG["openai_reasoning_effort"] == "medium"
    assert dc.DEFAULT_CONFIG["google_thinking_level"] == "minimal"
    assert dc.DEFAULT_CONFIG["anthropic_effort"] == "high"
    assert dc.DEFAULT_CONFIG["llm_provider"] == "openai"
    assert dc.DEFAULT_CONFIG["backend_url"] is None


def test_empty_env_value_is_passthrough(monkeypatch):
    """Empty TRADINGAGENTS_* values must not clobber the built-in default."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="",
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="",
        TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT="",
        TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT="",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "openai"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1
    assert dc.DEFAULT_CONFIG["deep_think_reasoning_effort"] is None
    assert dc.DEFAULT_CONFIG["quick_think_reasoning_effort"] is None


def test_empty_path_value_keeps_the_default_path(monkeypatch):
    """.env.example lists the path variables blank; uncommenting one made the
    path empty, and the graph failed creating its directories."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_RESULTS_DIR="",
        TRADINGAGENTS_CACHE_DIR="",
        TRADINGAGENTS_MEMORY_LOG_PATH="",
    )
    home = dc._TRADINGAGENTS_HOME
    assert dc.DEFAULT_CONFIG["results_dir"] == os.path.join(home, "logs")
    assert dc.DEFAULT_CONFIG["data_cache_dir"] == os.path.join(home, "cache")
    assert dc.DEFAULT_CONFIG["memory_log_path"] == os.path.join(home, "memory", "trading_memory.md")


def test_invalid_int_raises(monkeypatch):
    """Garbage int values should surface a ValueError at import, not silently misconfigure."""
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "not-a-number")
    with pytest.raises(ValueError, match="TRADINGAGENTS_MAX_DEBATE_ROUNDS"):
        importlib.reload(default_config_module)
    # Restore module state for subsequent tests in this process
    monkeypatch.delenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", raising=False)
    importlib.reload(default_config_module)


@pytest.mark.parametrize("bad", ["treu", "flase", "maybe", "2", "enabled"])
def test_invalid_bool_raises(monkeypatch, bad):
    """A misspelled boolean must fail loudly (like ints) instead of silently False."""
    monkeypatch.setenv("TRADINGAGENTS_CHECKPOINT_ENABLED", bad)
    with pytest.raises(ValueError, match="TRADINGAGENTS_CHECKPOINT_ENABLED"):
        importlib.reload(default_config_module)
    monkeypatch.delenv("TRADINGAGENTS_CHECKPOINT_ENABLED", raising=False)
    importlib.reload(default_config_module)


def test_unknown_env_var_is_ignored(monkeypatch):
    """Env vars outside _ENV_OVERRIDES must not bleed into DEFAULT_CONFIG."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_NONEXISTENT_KEY="oops",
    )
    assert "nonexistent_key" not in dc.DEFAULT_CONFIG
