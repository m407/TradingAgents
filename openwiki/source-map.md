---
type: Source Map
title: TradingAgents Source Map
description: Practical repository ownership map showing where engineers should start for graph runtime, agents, data vendors, model providers, CLI operations, persistence, reporting, and tests.
tags: [source-map, navigation, engineering]
---

# Source map

Use this map after selecting the relevant concept page. The [architecture overview](/openwiki/architecture/overview.md) explains component relationships, the [analysis workflow](/openwiki/workflows/analysis-run.md) explains execution, and the [testing guide](/openwiki/testing.md) maps changes to regressions.

## Entrypoints and configuration

| Path | Responsibility | Related concept |
|---|---|---|
| `README.md` | User-facing install, CLI/API examples, persistence, reproducibility, disclaimer | [Quickstart](/openwiki/quickstart.md) |
| `pyproject.toml` | Package metadata, dependencies, CLI entrypoint, pytest/Ruff configuration | [Operations](/openwiki/operations/runbook.md) |
| `main.py` | Minimal programmatic example | [Quickstart](/openwiki/quickstart.md) |
| `tradingagents/default_config.py` | Defaults, environment overlays, vendors, benchmarks, retry/debate settings | [Operations](/openwiki/operations/runbook.md) |
| `tradingagents/__init__.py` | dotenv loading precedence | [Operations](/openwiki/operations/runbook.md) |

## Graph runtime

| Path | Responsibility |
|---|---|
| `tradingagents/graph/trading_graph.py` | Composition root, client/tool setup, propagation, memory resolution, checkpoints, state/report persistence |
| `tradingagents/graph/setup.py` | LangGraph nodes, edges, analyst sequencing, debate/risk path maps |
| `tradingagents/graph/analyst_execution.py` | Analyst key validation, compatibility names, ordered execution plan |
| `tradingagents/graph/conditional_logic.py` | Tool-loop and debate termination routers |
| `tradingagents/graph/propagation.py` | Initial state and graph invocation configuration |
| `tradingagents/graph/checkpointer.py` | Per-ticker SQLite saver and thread identity |
| `tradingagents/graph/reflection.py` | LLM lesson generation for resolved decisions |
| `tradingagents/graph/signal_processing.py` | Final rating normalization |

Start with the [architecture overview](/openwiki/architecture/overview.md) before editing these files.

## Agents and domain contracts

| Path | Responsibility |
|---|---|
| `tradingagents/agents/analysts/` | Market, sentiment, news, and fundamentals evidence generation |
| `tradingagents/agents/researchers/` | Bull/bear debate turns |
| `tradingagents/agents/managers/` | Research and portfolio judgments |
| `tradingagents/agents/trader/` | Concrete transaction proposal |
| `tradingagents/agents/risk_mgmt/` | Aggressive, conservative, and neutral risk arguments |
| `tradingagents/agents/schemas.py` | Structured rating, proposal, PM, and sentiment contracts plus Markdown renderers |
| `tradingagents/agents/utils/agent_states.py` | Shared graph and debate state types |
| `tradingagents/agents/utils/structured.py` | Structured binding and free-text fallback |
| `tradingagents/agents/utils/memory.py` | Persistent pending/resolved decision log |
| `tradingagents/agents/utils/rating.py` | Five-tier rating parsing |

Read [Trading decision domain](/openwiki/domain/trading-decisions.md) before changing business semantics.

## Data integrations

| Path | Responsibility |
|---|---|
| `tradingagents/dataflows/interface.py` | Tool categories, vendor registry, routing, fallback, sentinel policy |
| `tradingagents/dataflows/config.py` | Active nested dataflow configuration |
| `tradingagents/dataflows/errors.py` | Typed vendor failures |
| `tradingagents/dataflows/y_finance.py` | Yahoo prices, fundamentals, insiders |
| `tradingagents/dataflows/stockstats_utils.py` | Cached OHLCV and technical-indicator date handling |
| `tradingagents/dataflows/yfinance_news.py` | UTC news-window semantics |
| `tradingagents/dataflows/alpha_vantage_*` | Alpha Vantage stock, fundamentals, indicators, and news |
| `tradingagents/dataflows/fred.py` | FRED macro indicators |
| `tradingagents/dataflows/polymarket.py` | Prediction-market enrichment |
| `tradingagents/dataflows/reddit.py` | Reddit sentiment source |
| `tradingagents/dataflows/stocktwits.py` | StockTwits sentiment source |
| `tradingagents/dataflows/symbol_utils.py` | Cross-source ticker normalization |
| `tradingagents/dataflows/market_data_validator.py` | Deterministic verified snapshot |

The routing and temporal contract is canonical in [Data and LLM integrations](/openwiki/integrations/data-and-llm.md).

## LLM integrations

| Path | Responsibility |
|---|---|
| `tradingagents/llm_clients/factory.py` | Native versus OpenAI-compatible selection |
| `tradingagents/llm_clients/openai_client.py` | Provider registry, endpoints, Responses/Chat behavior, compatibility wrappers |
| `tradingagents/llm_clients/capabilities.py` | Model-specific structured/reasoning behavior |
| `tradingagents/llm_clients/model_catalog.py` | Curated model choices |
| `tradingagents/llm_clients/api_key_env.py` | Provider credential environment names |
| `tradingagents/llm_clients/anthropic_client.py` | Native Anthropic client and effort |
| `tradingagents/llm_clients/google_client.py` | Native Gemini client and thinking level |
| `tradingagents/llm_clients/azure_client.py` | Azure OpenAI integration |
| `tradingagents/llm_clients/bedrock_client.py` | Bedrock SDK and auth selection |

## CLI, reports, and deployment

| Path | Responsibility |
|---|---|
| `cli/main.py` | Typer commands, interactive collection, Rich live execution, artifact writing, key prompting |
| `cli/utils.py` | Provider/model menus, backend resolution, localization utilities |
| `cli/stats_handler.py` | Runtime callback/stat tracking |
| `cli/static/` | Localized UI strings/assets |
| `tradingagents/reporting.py` | Shared final report-tree writer |
| `Dockerfile` | Non-root container build and entrypoint; currently references missing `uv.lock` |
| `docker-compose.yml` | Standard and Ollama profiles with persistent volumes |
| `.github/workflows/ci.yml` | Multi-version tests, clean install, Ruff gate |
| `scripts/smoke_structured_output.py` | Trusted-environment provider schema smoke |

Use the [operations runbook](/openwiki/operations/runbook.md) for execution and recovery guidance.

## Tests

Tests are organized by behavior rather than mirroring source directories. High-value anchors include:

- graph: `test_analyst_execution.py`, `test_checkpoint_resume.py`, `test_risk_router_path_map.py`;
- structured agents: `test_structured_agents.py`, `test_structured_agent_prompts.py`;
- persistence: `test_memory_log.py`, `test_reporting.py`;
- data safety: `test_news_lookahead.py`, `test_ohlcv_cache_freshness.py`, `test_alpha_vantage_hardening.py`;
- vendors: `test_vendor_routing.py`, `test_vendor_errors.py`;
- providers: `test_provider_registry.py`, `test_capabilities.py`, provider-specific tests;
- CLI/config: `test_cli_config_precedence.py`, `test_cli_env_skip.py`, `test_cli_no_console.py`.

The full change-oriented matrix is in the [testing guide](/openwiki/testing.md).

## Recent history to know

Recent releases shifted the repository toward explicit correctness contracts:

- v0.3.0 introduced the provider/vendor registries, CI gate, verified data access, report API, and strict config precedence.
- v0.3.1 hardened look-ahead filtering, router safety, graph-shape checkpointing, crypto sentiment mapping, provider retry budgets, and Bedrock auth.
- Subsequent fixes refreshed same-day OHLCV caches, made Yahoo news UTC/end-exclusive, prevented schema-only agents from requesting unavailable tools, and unified report writing.
- Local HEAD `b5d86f4` changed only the Dockerfile toward uv-frozen builds and currently lacks its referenced lockfile.

Use targeted `git log -- <path>` and `git show <commit> -- <path>` when a rule appears unusually defensive; many such rules correspond directly to regression tests and changelog entries.
