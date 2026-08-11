---
type: Operations Runbook
title: TradingAgents Operations Runbook
description: Practical configuration, execution, persistence, checkpoint recovery, container, artifact, and troubleshooting guidance for TradingAgents.
tags: [operations, configuration, docker, recovery]
openwiki:
  roles: [operations]
  change_kinds: [configuration, deployment, recovery]
  source_paths: [tradingagents/default_config.py, cli/main.py, Dockerfile, docker-compose.yml]
  symbols: [DEFAULT_CONFIG, TradingAgentsGraph._get_provider_kwargs, _build_run_config]
  test_paths: [tests/test_env_overrides.py, tests/test_openai_reasoning_effort.py, tests/test_checkpoint_resume.py]
  invariants: [Per-model reasoning settings override the shared OpenAI setting., Successful checkpointed runs clear only their matching checkpoint rows.]
  validation_commands: [pytest -q tests/test_env_overrides.py tests/test_openai_reasoning_effort.py]
---

# Operations runbook

This runbook operates the [analysis workflow](/openwiki/workflows/analysis-run.md) and configures the [data and LLM integrations](/openwiki/integrations/data-and-llm.md). For code ownership, use the [source map](/openwiki/source-map.md).

## Configuration precedence

The effective configuration is assembled in layers:

1. Existing exported process variables retain priority.
2. Package import loads `.env`, then `.env.enterprise`, only for values not already set (`tradingagents/__init__.py`).
3. `DEFAULT_CONFIG` applies `TRADINGAGENTS_*` overrides with type-aware coercion (`default_config.py`). Invalid booleans and numbers fail at startup rather than silently falling back.
4. A programmatic `config` passed to `TradingAgentsGraph` becomes the active dataflow configuration; nested vendor dictionaries merge one level (`graph/trading_graph.py`, `dataflows/config.py`).
5. In the CLI, explicit environment settings or explicit flags win; otherwise interactive choices apply (`cli/main.py`).

High-value variables include provider/model/backend, output language, debate and risk round counts, checkpoint enablement, benchmark, temperature, retry budget, results/cache/memory paths, and provider-specific reasoning controls. Treat `.env.example` and `default_config.py` as the canonical non-secret references.

### Quick and deep OpenAI reasoning effort

For `openai` and `openai_compatible`, unattended or programmatic runs can tune the two model roles separately:

```bash
export TRADINGAGENTS_OPENAI_REASONING_EFFORT=medium
export TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT=low
export TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT=high
```

The quick/deep values override the shared value only for their matching client; unset values inherit the shared setting, and all three unset leaves the provider default. The graph applies this before `create_llm_client()` and also forwards temperature, retry budget, and callbacks independently to each client (`TradingAgentsGraph._get_provider_kwargs`). Known non-reasoning native OpenAI models still discard the parameter in `OpenAIClient`.

The interactive CLI currently collects one shared OpenAI effort and writes `openai_reasoning_effort` into the run config. It does not prompt for separate quick/deep values; however, `_build_run_config()` starts from `DEFAULT_CONFIG.copy()`, so per-thinker environment overrides remain present and take precedence during graph construction. Use those environment variables or pass a Python `config` when the two roles must differ. Validate changes with `pytest -q tests/test_env_overrides.py tests/test_openai_reasoning_effort.py`.

## Run modes

### Interactive CLI

```bash
tradingagents
tradingagents analyze --checkpoint
tradingagents analyze --clear-checkpoints
```

The installed entrypoint is `cli.main:app` (`pyproject.toml`). If the terminal cannot support the Rich/prompt-toolkit UI, the CLI reports an unusable console rather than exposing a prompt-toolkit traceback (`tests/test_cli_no_console.py`, commit `3f6c082`).

### Python API

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
config["checkpoint_enabled"] = True
agent = TradingAgentsGraph(config=config)
state, rating = agent.propagate("NVDA", "2026-01-15")
agent.save_reports("NVDA", "./reports")
```

The shared report writer gives CLI and API runs the same report structure (`tradingagents/reporting.py`, commit `a0120e1`).

## Persistent artifacts

| Artifact | Default/shape | Purpose |
|---|---|---|
| Decision memory | `~/.tradingagents/memory/trading_memory.md` | Pending decisions, outcomes, reflections, future PM context |
| Cache | `~/.tradingagents/cache` | Market caches and checkpoint databases |
| Checkpoints | `<cache>/checkpoints/<SAFE_TICKER>.db` | Opt-in node-level crash recovery |
| Full-state logs | `<results>/<SAFE_TICKER>/TradingAgentsStrategy_logs/full_states_log_<date>.json` | Completed graph state diagnostics |
| CLI live run | `<results>/<ticker>/<date>/...` | Message/tool log and incremental reports |
| Saved report tree | caller-selected path with `1_analysts` through `5_portfolio` | Human-readable final projection |

Paths are configurable through `TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR`, and `TRADINGAGENTS_MEMORY_LOG_PATH`. Ticker-derived filesystem components are sanitized.

## Checkpoint recovery

Checkpointing is disabled by default. When enabled, one SQLite database is used per ticker to reduce cross-ticker contention. A thread ID incorporates ticker, date, analyst order, debate/risk depth, and asset type. A matching interrupted run resumes the latest successful node; a successful run clears its rows (`graph/checkpointer.py`, `tests/test_checkpoint_resume.py`).

Operational cautions:

- Provider/model, prompt, language, vendor, and benchmark settings are not in the checkpoint signature.
- Simultaneous same-ticker processes share one database; there is no documented concurrency guarantee.
- Cleanup ignores SQLite `OperationalError`, so stale rows can remain.
- Persistence failures after graph completion leave checkpoints in place, potentially replaying finalization on resume.

Use `--clear-checkpoints` before a run when graph semantics changed outside the signature or when recovery state is suspect.

## Decision memory operations

Memory is on by default. Pending decisions are resolved only during a later run for the same ticker. The log uses atomic replacement for updates, but no explicit inter-process lock is present. Avoid concurrent writers to the same memory file unless external serialization is provided.

If `memory_log_max_entries` is configured, only oldest resolved entries are pruned; pending entries are retained. Changing the log format requires backward-compatible parser tests in `tests/test_memory_log.py`.

## Docker and Compose

Compose defines:

- `tradingagents`: interactive container with `.env` and a persistent application volume;
- `ollama`: optional local model service;
- `tradingagents-ollama`: application profile configured for the Ollama service.

The runtime image uses non-root `appuser` and persists `/home/appuser/.tradingagents` (`Dockerfile`, `docker-compose.yml`).

### Container validation boundary

The current `Dockerfile` uses a Python 3.12 builder stage, creates `/opt/venv`, runs `pip install --no-cache-dir .`, copies that environment into the runtime stage, and runs as non-root `appuser`. It no longer depends on `uv.lock`; the earlier frozen-uv build blocker is resolved in the current source.

CI still verifies only editable test installs, a clean pip install/import, and Ruff. It does not build the image or start Compose services. Therefore:

- ordinary Python changes do not require a container build;
- changes to `Dockerfile`, package runtime dependencies, package data, entrypoints, or `docker-compose.yml` require `docker build .`;
- changes to Ollama service wiring or volumes additionally require the affected Compose profile in an environment that can start containers.

Do not treat `pip install .` passing as shipped-container correctness: the consumer-facing boundary is a successful image build and `tradingagents` entrypoint startup.

## Troubleshooting

### Analysis stops on missing data

Read the returned sentinel and logs. `NO_DATA_AVAILABLE` means all configured vendors found no usable/stale coverage; do not add model-side guessing. A raised error means selected core vendors failed operationally. Verify the exact configured chain and required API keys.

### Historical output changes between runs

This is expected for LLM sampling and live news/social sources. Pinning the analysis date fixes price/indicator windows, not the live textual evidence. Lower temperature may reduce variation for models that honor it; reasoning models remain non-deterministic.

### Structured result falls back to prose

Look for warnings from `agents/utils/structured.py`. Check the provider capability rules, model ID, endpoint, and the focused structured-agent tests. Free-text fallback may cause final rating parsing to default to Hold.

### Run exhausts recursion or is too expensive

Reduce selected analysts, debate rounds, risk rounds, or repeated tool requests. Analysts are sequential. `max_recur_limit` defaults to 100.

### Provider returns authentication or endpoint errors

Confirm the selected provider, its documented environment-variable name, and endpoint precedence. For Bedrock, ensure the optional extra is installed and use either bearer-token auth or a valid AWS credential chain. Never print keys while debugging.

## Release checks

Follow the [testing guide](/openwiki/testing.md), verify a clean package install, and inspect `git status` for generated results or credential files. Container changes require `docker build .`; integration changes should run the relevant real-provider smoke only in a trusted environment with credentials supplied by environment variables.
