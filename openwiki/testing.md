---
type: Testing Guide
title: TradingAgents Testing Guide
description: Test strategy and change-oriented verification guide for the TradingAgents graph, agents, data vendors, LLM providers, CLI, persistence, reports, and containers.
tags: [testing, pytest, ruff, ci]
openwiki:
  roles: [testing]
  change_kinds: [validation, provider, configuration]
  source_paths: [pyproject.toml, .github/workflows/ci.yml]
  test_paths: [tests/test_env_overrides.py, tests/test_openai_reasoning_effort.py]
  validation_commands: [pytest -q, ruff check .]
---

# Testing guide

The test suite is primarily isolated regression coverage with mocks, supported by strict Ruff linting, a Python 3.10-3.13 CI matrix, a clean-install import smoke, and an optional real-provider structured-output script (`pyproject.toml`, `.github/workflows/ci.yml`). It verifies the [architecture](/openwiki/architecture/overview.md), [analysis workflow](/openwiki/workflows/analysis-run.md), and [integration contracts](/openwiki/integrations/data-and-llm.md).

## Standard checks

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
```

Pytest declares `unit`, `integration`, and `smoke` markers, although much of the current suite runs without markers. Useful selectors are `pytest -m unit`, `pytest -m integration`, and `pytest -m smoke` when tests are classified (`pyproject.toml`).

CI runs:

- the full pytest suite on Python 3.10, 3.11, 3.12, and 3.13;
- `pip install .` followed by imports of `tradingagents` and `cli.main` on Python 3.12;
- `ruff check .` over the repository.

`tests/conftest.py` installs placeholder API keys and resets global dataflow configuration so most tests do not call external services.

## Change-oriented test map

| Change area | Start with |
|---|---|
| Graph shape, analyst ordering, routing | `test_analyst_execution.py`, `test_risk_router_path_map.py` |
| Checkpoint signature or recovery | `test_checkpoint_resume.py`, `test_llm_max_retries.py` |
| Structured manager/trader/sentiment behavior | `test_structured_agents.py`, `test_structured_agent_prompts.py`, `test_temperature_config.py` |
| Rating parsing and final signal | `test_signal_processing.py` |
| Memory/reflection/benchmark behavior | `test_memory_log.py` |
| Report tree | `test_reporting.py` |
| Vendor routing and error taxonomy | `test_vendor_routing.py`, `test_vendor_errors.py`, `test_no_data_handling.py` |
| Yahoo date/cache/stale rules | `test_date_boundaries.py`, `test_news_lookahead.py`, `test_ohlcv_cache_freshness.py`, `test_yfinance_stale_ohlcv_guard.py` |
| Alpha Vantage hardening | `test_alpha_vantage_hardening.py` |
| FRED or Polymarket | `test_fred.py`, `test_polymarket.py` |
| Symbol, identity, crypto, path safety | `test_symbol_utils.py`, `test_instrument_identity.py`, `test_crypto_asset_mode.py`, `test_safe_ticker_component.py` |
| Provider registry and capabilities | `test_provider_registry.py`, `test_capabilities.py`, `test_model_validation.py` |
| Provider endpoint/key/reasoning behavior | `test_api_key_env.py`, `test_ollama_base_url.py`, `test_openai_reasoning_effort.py`, provider-specific tests |
| Environment override precedence and coercion | `test_env_overrides.py`, plus the owning CLI/provider test when the value crosses that boundary |
| CLI configuration and terminal behavior | `test_cli_config_precedence.py`, `test_cli_env_skip.py`, `test_cli_no_console.py`, `test_cli_symbol_handling.py` |
| Localization | `test_i18n_coverage.py` |

Run a focused file while iterating, then the complete suite before merging.

## Integration and smoke testing

`tests/test_deepseek_reasoning.py` contains a live integration case that runs only with a real key. Do not hardcode or print credentials.

For real-provider schema compatibility:

```bash
python scripts/smoke_structured_output.py <provider>
```

The script exercises structured output against configured providers and is useful after capability, schema, or provider-client changes. Run it only in a trusted environment where required credentials are already exported.

## What to assert for common changes

### Agent or prompt change

- Expected tools are bound and executable by the matching `ToolNode`.
- Schema-only prompts forbid unavailable external tools.
- Current date and instrument context remain present.
- Structured success and free-text fallback both work.
- Stable Markdown headings remain compatible with reports and memory.

### Dataflow change

- Exact vendor selection and ordered fallback are preserved.
- Errors are classified as no-data, not-configured, rate-limit, or unexpected.
- Historical date boundaries are explicit and tested at the edge.
- Stale/empty data cannot become fabricated model evidence.
- Source-specific symbol normalization covers stocks and crypto.

### Graph or persistence change

- Initial state contains every field nodes expect.
- Debate path maps cover every router return.
- Resume uses a signature that reflects all graph-shaping choices.
- Successful completion clears only the intended checkpoint.
- State/report/memory writes remain ordered and failure-safe.

### Provider change

- Factory/registry selection, endpoint precedence, key lookup, and model validation are covered.
- Capability quirks are gated to the intended models.
- Cross-provider `temperature` and retry behavior remains optional.
- Structured-output tests cover both parsed and malformed responses.

### Model-construction configuration change

- The default remains `None` when omission is meant to preserve provider behavior.
- Environment overrides map to the exact config key in `test_env_overrides.py`.
- Quick and deep clients receive independent kwargs; per-role values override shared fallbacks without mutating each other.
- The real provider wrapper drops or transforms unsupported parameters, not just the graph helper.
- Interactive CLI precedence is tested if the setting is exposed there; per-thinker OpenAI effort is currently environment/programmatic only.

The narrow check for the current per-model effort seam is:

```bash
pytest -q tests/test_env_overrides.py tests/test_openai_reasoning_effort.py
```

Run `python scripts/smoke_structured_output.py <provider>` conditionally only if request/schema compatibility changed and trusted credentials are available.

In a fresh checkout, install development dependencies before these tests. If collection fails with a missing runtime package such as `langchain_core`, the environment is not provisioned; that is not a product-test failure.

## Current gaps

- CI does not build the Docker image, so Dockerfile, package-copy, entrypoint, user/permission, and Compose regressions require an explicit local image build.
- Historical news/social reproducibility cannot be proven without archived input fixtures or snapshots.
- Marker classification is not comprehensive; the full suite is the reliable gate.
- Most provider behavior is mocked; a release touching protocol compatibility should supplement unit tests with targeted trusted-environment smoke runs.

See the [operations runbook](/openwiki/operations/runbook.md) for the container blocker and release procedure.
