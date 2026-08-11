---
type: Project Guide
title: TradingAgents Quickstart
description: Entry point for engineers working on TradingAgents, covering purpose, setup, first runs, repository navigation, and current operational caveats.
tags: [tradingagents, quickstart, engineering]
openwiki:
  roles: [repository]
  change_kinds: [configuration, lifecycle, integration, testing]
  source_paths: [tradingagents/graph/trading_graph.py, tradingagents/default_config.py, cli/main.py]
  symbols: [TradingAgentsGraph, DEFAULT_CONFIG]
  test_paths: [tests/test_analyst_execution.py, tests/test_env_overrides.py, tests/test_openai_reasoning_effort.py]
  validation_commands: [pytest -q]
---

# TradingAgents quickstart

TradingAgents is a research-oriented, multi-agent financial analysis framework. A LangGraph workflow assigns market, sentiment, news, and fundamentals research to specialist LLM agents; runs bull/bear and risk debates; asks a trader for an executable proposal; and lets a portfolio manager issue a final five-tier position rating. It supports an interactive CLI and a Python API, but it is explicitly not financial advice (`README.md`, `tradingagents/graph/setup.py`).

## Start here

| Change area or user intent | Relevant wiki page | Exact source entry points | Important symbols or types | Focused tests | Minimal validation command |
|---|---|---|---|---|---|
| Change graph shape, analyst order, or debate routing | [Architecture overview](/openwiki/architecture/overview.md) | `tradingagents/graph/setup.py`, `analyst_execution.py`, `conditional_logic.py` | `GraphSetup.setup_graph`, `build_analyst_execution_plan`, `ConditionalLogic` | `tests/test_analyst_execution.py`, `tests/test_risk_router_path_map.py` | `pytest -q tests/test_analyst_execution.py tests/test_risk_router_path_map.py` |
| Change run state, persistence order, or checkpoint recovery | [Analysis run workflow](/openwiki/workflows/analysis-run.md) | `tradingagents/graph/trading_graph.py`, `propagation.py`, `checkpointer.py` | `TradingAgentsGraph.propagate`, `Propagator`, `thread_id` | `tests/test_checkpoint_resume.py`, `tests/test_reporting.py`, `tests/test_memory_log.py` | `pytest -q tests/test_checkpoint_resume.py tests/test_reporting.py tests/test_memory_log.py` |
| Change analyst roles, ratings, debates, or memory semantics | [Trading decision domain](/openwiki/domain/trading-decisions.md) | `tradingagents/agents/schemas.py`, `agent_states.py`, `memory.py` | `ResearchPlan`, `TraderProposal`, `PortfolioDecision`, `AgentState`, `TradingMemoryLog` | `tests/test_structured_agents.py`, `tests/test_signal_processing.py`, `tests/test_memory_log.py` | `pytest -q tests/test_structured_agents.py tests/test_signal_processing.py tests/test_memory_log.py` |
| Add or modify a data vendor or LLM provider | [Data and LLM integrations](/openwiki/integrations/data-and-llm.md) | `tradingagents/dataflows/interface.py`, `tradingagents/llm_clients/factory.py`, `openai_client.py` | `VENDOR_METHODS`, `create_llm_client`, `OpenAIClient` | `tests/test_vendor_routing.py`, `tests/test_provider_registry.py`, `tests/test_capabilities.py` | `pytest -q tests/test_vendor_routing.py tests/test_provider_registry.py tests/test_capabilities.py` |
| Tune quick/deep reasoning effort or provider construction | [Architecture overview](/openwiki/architecture/overview.md), [Operations runbook](/openwiki/operations/runbook.md) | `tradingagents/default_config.py`, `tradingagents/graph/trading_graph.py` | `DEFAULT_CONFIG`, `TradingAgentsGraph._get_provider_kwargs` | `tests/test_env_overrides.py`, `tests/test_openai_reasoning_effort.py` | `pytest -q tests/test_env_overrides.py tests/test_openai_reasoning_effort.py` |
| Configure, run, recover, or package the system | [Operations runbook](/openwiki/operations/runbook.md) | `cli/main.py`, `tradingagents/default_config.py`, `Dockerfile`, `docker-compose.yml` | `_build_run_config`, `DEFAULT_CONFIG`, `TradingAgentsGraph` | `tests/test_cli_config_precedence.py`, `tests/test_env_overrides.py` | `pytest -q tests/test_cli_config_precedence.py tests/test_env_overrides.py` |
| Select broader checks or find analogous regressions | [Testing guide](/openwiki/testing.md) | `tests/`, `pyproject.toml`, `.github/workflows/ci.yml` | pytest markers and Ruff configuration | Change-specific files listed in the guide | `pytest -q <focused-test-files>` |
| Locate ownership before editing | [Source map](/openwiki/source-map.md) | Repository entrypoints listed by subsystem | Owning APIs and implementation symbols | Linked from each subsystem row | Use the command from the owning concept page |

The [architecture](/openwiki/architecture/overview.md) dispatches an [analysis run](/openwiki/workflows/analysis-run.md), which applies the [trading decision rules](/openwiki/domain/trading-decisions.md), calls [data and LLM integrations](/openwiki/integrations/data-and-llm.md), and writes artifacts managed through the [operations runbook](/openwiki/operations/runbook.md).

## Local setup

The package requires Python 3.10 or newer. The repository README currently documents a pip workflow, and CI verifies it on Python 3.10-3.13 (`pyproject.toml`, `.github/workflows/ci.yml`).

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Configure one LLM provider without committing secrets. Copy `.env.example` to `.env` locally or export the provider key named in the README. Do not place credentials in source or OpenWiki pages.

Run the interactive application:

```bash
tradingagents
# Equivalent source invocation:
python -m cli.main
```

The CLI collects ticker, analysis date, analyst selection, debate depth, provider, models, output language, and optional checkpoint settings (`cli/main.py`).

## Minimal Python run

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
graph = TradingAgentsGraph(config=config)
state, rating = graph.propagate("NVDA", "2026-01-15")
print(rating)
```

`propagate()` returns the final graph state and a normalized rating. Use `TradingAgentsGraph.save_reports(...)` to project the state into the same hierarchical Markdown report tree used by the CLI (`tradingagents/graph/trading_graph.py`, `tradingagents/reporting.py`).

## Engineering mental model

1. **Resolve identity and context.** The ticker is normalized and resolved before agents run, including stock-versus-crypto context.
2. **Collect evidence.** Selected analysts execute sequentially. Tool-using analysts loop until they stop requesting data; the sentiment analyst prefetches its sources directly.
3. **Debate direction.** Bull and bear researchers alternate for a configured number of rounds; the Research Manager emits a five-tier recommendation.
4. **Propose a trade.** The Trader converts research into Buy, Hold, or Sell plus optional levels and sizing.
5. **Debate risk.** Aggressive, conservative, and neutral agents rotate; the Portfolio Manager emits the final five-tier rating.
6. **Persist results.** The run writes state/report artifacts, appends a pending decision-memory entry, and clears successful checkpoints.

See the [analysis workflow](/openwiki/workflows/analysis-run.md) for the grounded sequence and diagrams.

## Important current caveats

- **Container builds are not exercised by CI.** The current multi-stage `Dockerfile` installs the package with pip into a builder virtual environment and copies it into a non-root runtime image, but `.github/workflows/ci.yml` does not run `docker build`. Validate container or Compose changes locally.
- **Per-model reasoning settings are primarily a non-interactive/programmatic seam.** `TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT` and `TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT` override the shared OpenAI effort value during graph construction. The interactive CLI still asks for one shared OpenAI value, so use environment variables or a Python config when quick and deep models need different effort.
- **Historical runs are not fully reproducible.** Price and indicator windows are date-filtered, but live news, StockTwits, and Reddit inputs can change for the same historical date (`README.md`).
- **Analysts are sequential.** The former concurrency setting was removed as a no-op in v0.3.0; latency and cost increase with analyst tool calls and debate rounds (`CHANGELOG.md`).
- **Structured output is fail-open.** Selected agents prefer Pydantic schemas but retry with free text when schema binding or parsing fails (`tradingagents/agents/utils/structured.py`).

## Before submitting changes

Run at least:

```bash
pytest -q
ruff check .
```

Then select focused regression tests from the [testing guide](/openwiki/testing.md). Changes to Docker should additionally run `docker build .`; Compose or Ollama-profile changes should exercise the affected profile because CI does not cross that packaging boundary.

## Backlog

- **CLI presentation and localization** — `cli/main.py`, `cli/static/`, `tests/test_i18n_coverage.py`: deferred as a separate UI domain because the first pass prioritizes runtime and operational behavior.
- **Social-source transport internals** — `tradingagents/dataflows/reddit.py`, `stocktwits.py`: routing and business effects are documented, but detailed RSS/backoff/parsing behavior is deferred.
- **Model-by-model catalog reference** — `tradingagents/llm_clients/model_catalog.py`: providers and extension rules are documented; the fast-changing model ID table is best read from source.
