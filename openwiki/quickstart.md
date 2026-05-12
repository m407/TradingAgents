---
type: Project Guide
title: TradingAgents Quickstart
description: Entry point for engineers working on TradingAgents, covering purpose, setup, first runs, repository navigation, and current operational caveats.
tags: [tradingagents, quickstart, engineering]
---

# TradingAgents quickstart

TradingAgents is a research-oriented, multi-agent financial analysis framework. A LangGraph workflow assigns market, sentiment, news, and fundamentals research to specialist LLM agents; runs bull/bear and risk debates; asks a trader for an executable proposal; and lets a portfolio manager issue a final five-tier position rating. It supports an interactive CLI and a Python API, but it is explicitly not financial advice (`README.md`, `tradingagents/graph/setup.py`).

## Start here

| Need | Read |
|---|---|
| Understand components and graph topology | [Architecture overview](/openwiki/architecture/overview.md) |
| Follow one analysis from ticker to saved result | [Analysis run workflow](/openwiki/workflows/analysis-run.md) |
| Understand analyst roles, debates, ratings, and learning memory | [Trading decision domain](/openwiki/domain/trading-decisions.md) |
| Work on market-data vendors or model providers | [Data and LLM integrations](/openwiki/integrations/data-and-llm.md) |
| Configure, run, recover, or troubleshoot the system | [Operations runbook](/openwiki/operations/runbook.md) |
| Choose tests for a change | [Testing guide](/openwiki/testing.md) |
| Find the owning source files | [Source map](/openwiki/source-map.md) |

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

- **Container build is currently inconsistent.** HEAD changed `Dockerfile` to `uv sync --frozen` and `COPY uv.lock`, but the repository does not contain `uv.lock`. Until a lockfile is restored/generated or the Dockerfile is adjusted, expect `docker build` and Compose builds to fail at the copy step. This follows commit `b5d86f4`; CI does not build the image.
- **Historical runs are not fully reproducible.** Price and indicator windows are date-filtered, but live news, StockTwits, and Reddit inputs can change for the same historical date (`README.md`).
- **Analysts are sequential.** The former concurrency setting was removed as a no-op in v0.3.0; latency and cost increase with analyst tool calls and debate rounds (`CHANGELOG.md`).
- **Structured output is fail-open.** Selected agents prefer Pydantic schemas but retry with free text when schema binding or parsing fails (`tradingagents/agents/utils/structured.py`).

## Before submitting changes

Run at least:

```bash
pytest -q
ruff check .
```

Then select focused regression tests from the [testing guide](/openwiki/testing.md). Changes to Docker should additionally run a real image build once the missing-lockfile issue is resolved.

## Backlog

- **CLI presentation and localization** — `cli/main.py`, `cli/static/`, `tests/test_i18n_coverage.py`: deferred as a separate UI domain because the first pass prioritizes runtime and operational behavior.
- **Social-source transport internals** — `tradingagents/dataflows/reddit.py`, `stocktwits.py`: routing and business effects are documented, but detailed RSS/backoff/parsing behavior is deferred.
- **Model-by-model catalog reference** — `tradingagents/llm_clients/model_catalog.py`: providers and extension rules are documented; the fast-changing model ID table is best read from source.
