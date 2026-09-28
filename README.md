<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>
<br>
<div align="center">
  <a href="https://github.com/TauricResearch" target="_blank"><img alt="TradingAgents #1 Repository of the Day" src="https://trendshift.io/api/badge/repositories/16192" width="250" height="55"/></a>
</div>
<br>
<div align="center">
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=de">Deutsch</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=es">Español</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=fr">français</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ja">日本語</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ko">한국어</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=pt">Português</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ru">Русский</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=zh">中文</a>
</div>

---

# TradingAgents: Multi-Agents LLM Financial Trading Framework

## News

<!-- news:start -->
- [2026-09] **TradingAgents v0.5.1** released with a package layout organised by what each module holds (import paths moved), optional Jev screening of social posts, GPT-6 Sol and Luna as the default models, and fixes to run isolation and SEC EDGAR statements.
- [2026-09] **TradingAgents v0.5.0** released with point-in-time integrity across every dated path, SEC EDGAR fundamentals served as filed, backtesting over a ticker and date grid, portfolio-aware runs, and current model lineups across every provider.
- [2026-08] **TradingAgents v0.4.0** released with look-ahead / point-in-time fixes across FRED macro, social sentiment, and the decision-log memory; clearer decision signals; working CLI checkpoint resume; Trader price grounding; and the GPT-5.6 and GLM-5.3 models.

Full release notes are in [CHANGELOG.md](CHANGELOG.md).

<details>
<summary>Earlier news</summary>

- [2026-07] **TradingAgents v0.3.1** released with correctness and stability fixes: Alpha Vantage look-ahead filtering, graph-router crash-safety, graph-shape-aware checkpoint resume, working crypto sentiment sources, a configurable LLM retry budget, Bedrock API-key auth, and Claude Sonnet 5 / Fable 5 support.
- [2026-06] **TradingAgents v0.3.0** released with a verified data-access contract, an expanded provider registry (NVIDIA, Kimi, Groq, Mistral, Bedrock, and any OpenAI-compatible endpoint), FRED and Polymarket data vendors, a current-generation model catalog, and a CI gate.
- [2026-05] **TradingAgents v0.2.5** released with the grounded Sentiment Analyst, GPT-5.5 etc. model coverage, Qwen/GLM/MiniMax dual-region support, `TRADINGAGENTS_*` env-var configurability with API-key auto-detection, remote Ollama support, non-US alpha benchmarks, and ticker path-traversal hardening.
- [2026-04] **TradingAgents v0.2.4** released with structured-output agents (Research Manager, Trader, Portfolio Manager), LangGraph checkpoint resume, persistent decision log, DeepSeek/Qwen/GLM/Azure provider support, Docker, and a Windows UTF-8 encoding fix.
- [2026-03] **TradingAgents v0.2.3** released with multi-language support, GPT-5.4 family models, unified model catalog, backtesting date fidelity, and proxy support.
- [2026-03] **TradingAgents v0.2.2** released with GPT-5.4/Gemini 3.1/Claude 4.6 model coverage, five-tier rating scale, OpenAI Responses API, Anthropic effort control, and cross-platform stability.
- [2026-02] **TradingAgents v0.2.0** released with multi-provider LLM support (GPT-5.x, Gemini 3.x, Claude 4.x, Grok 4.x) and improved system architecture.
- [2026-01] **Trading-R1** [Technical Report](https://arxiv.org/abs/2509.11420) released, with [Terminal](https://github.com/TauricResearch/Trading-R1) expected to land soon.

</details>
<!-- news:end -->

<div align="center">

🚀 [TradingAgents](#tradingagents-framework) | ⚡ [Installation & CLI](#installation-and-cli) | 🎬 [Demo](https://www.youtube.com/watch?v=90gr5lwjIho) | 📦 [Package Usage](#tradingagents-package) | 🤝 [Contributing](#contributing) | 📄 [Citation](#citation)

</div>

> 🎉 **TradingAgents** officially released! We have received numerous inquiries about the work, and we would like to express our thanks for the enthusiasm in our community.
>
> So we decided to fully open-source the framework. Looking forward to building impactful projects with you!

## TradingAgents Framework

TradingAgents is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. By deploying specialized LLM-powered agents: from fundamental analysts, sentiment experts, and technical analysts, to trader, risk management team, the platform collaboratively evaluates market conditions and informs trading decisions. Moreover, these agents engage in dynamic discussions to pinpoint the optimal strategy.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> TradingAgents framework is designed for research purposes. Trading performance may vary based on many factors, including the chosen backbone language models, model temperature, trading periods, the quality of data, and other non-deterministic factors. [It is not intended as financial, investment, or trading advice.](https://tauric.ai/disclaimer/)

Our framework decomposes complex trading tasks into specialized roles.

### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Aggregates news headlines, StockTwits, and Reddit chatter into a single sentiment read to gauge short-term market mood.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions, determining the timing and magnitude of trades.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

## Installation and CLI

### Installation

Clone TradingAgents:
```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
```

Create a virtual environment in any of your favorite environment managers:
```bash
conda create -n tradingagents python=3.12
conda activate tradingagents
```

Or with [uv](https://docs.astral.sh/uv/):
```bash
uv venv --python 3.12
source .venv/bin/activate
```

Install the package and its dependencies (`uv pip install .` with uv):
```bash
pip install .
```

### Docker

Alternatively, run with Docker:
```bash
cp .env.example .env  # add your API keys
docker compose run --rm tradingagents
```

After updating the repository, rebuild the image with `docker compose build`.

For local models with Ollama:
```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen — International (dashscope-intl.aliyuncs.com)
export DASHSCOPE_CN_API_KEY=...    # Qwen — China (dashscope.aliyuncs.com)
export ZHIPU_API_KEY=...           # GLM via Z.AI (international)
export ZHIPU_CN_API_KEY=...        # GLM via BigModel (China, open.bigmodel.cn)
export MINIMAX_API_KEY=...         # MiniMax — Global (api.minimax.io)
export MINIMAX_CN_API_KEY=...      # MiniMax — China (api.minimaxi.com)
export OPENROUTER_API_KEY=...      # OpenRouter
export MISTRAL_API_KEY=...         # Mistral
export MOONSHOT_API_KEY=...        # Kimi (Moonshot)
export GROQ_API_KEY=...            # Groq
export NVIDIA_API_KEY=...          # NVIDIA NIM
export FRED_API_KEY=...            # FRED macro data (free, optional)
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
export TYPESAFE_API_KEY=...        # Jev social-post screening (optional)
```

For Azure OpenAI, copy `.env.enterprise.example` to `.env.enterprise` and fill in your credentials.

For AWS Bedrock, install the extra with `pip install ".[bedrock]"`, set `llm_provider: "bedrock"`, configure AWS credentials (environment variables, `~/.aws/credentials`, or an IAM role) and `AWS_DEFAULT_REGION`, and use a Bedrock model ID, e.g. `us.anthropic.claude-opus-4-8-v1:0`.

For local models, configure Ollama with `llm_provider: "ollama"`. The default endpoint is `http://localhost:11434/v1`; set `OLLAMA_BASE_URL` to point at a remote `ollama-serve`. Pull models with `ollama pull <name>`, and pick "Custom model ID" in the CLI for any model not listed by default.

For any other OpenAI-compatible server (vLLM, LM Studio, llama.cpp, or a custom relay), use `llm_provider: "openai_compatible"` and set the endpoint via `backend_url` (or `TRADINGAGENTS_LLM_BACKEND_URL`), e.g. `http://localhost:8000/v1` for vLLM or `http://localhost:1234/v1` for LM Studio. The model is whatever your server serves. No key is needed for local servers; set `OPENAI_COMPATIBLE_API_KEY` when the endpoint requires one.

With `TYPESAFE_API_KEY` set, the Sentiment Analyst screens StockTwits and Reddit posts with TypeSafe's Jev before reading them. Posts that are not about the company are dropped, and each source opens with a count of the remaining posts by stance: bullish, bearish, neutral, or unclear. Without the key, posts pass through unscreened. `jev-latest` moves with new releases; set `TYPESAFE_DEFAULT_MODEL` to a versioned ID such as `jev-1.13.0` to hold it fixed across runs.

Alternatively, copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

### Tradernet Global credentials with fnox

The root [`fnox.toml`](fnox.toml) stores references to Tradernet Global API
credentials in the OS keychain under the service `tradingagents-tradernet-global`.
The configuration can be committed; the actual credentials stay in the keychain.
This prepares credential storage for the planned `tradernet-sdk` news adapter;
it does not enable Tradernet news retrieval by itself.

Install [fnox](https://fnox.jdx.dev/) separately from the Python dependencies.
On Linux, its [keychain provider](https://fnox.jdx.dev/providers/keychain)
requires a running, unlocked Secret Service such as GNOME Keyring. Headless
sessions need access to that service as well.

Create an API key pair in the
[Tradernet Global API key page](https://tradernet.global/tradernet-api/auth-api).
Save the private key when it is first displayed; trading activation is not
needed for news access. From the repository root, store both values using
hidden interactive prompts:

```bash
fnox set TRADERNET_PUBLIC_KEY --provider tradernet_keychain
fnox set TRADERNET_PRIVATE_KEY --provider tradernet_keychain
```

Omit the value arguments as shown, so credentials do not enter shell history.
Do not put either value in `fnox.toml` or commit them to the repository.
After storing them, check availability and launch with injected environment
variables:

```bash
fnox check
fnox exec -- uv run tradingagents
```

Both entries are required for this fnox configuration. Ordinary launches without
`fnox exec` do not require Tradernet credentials. The planned adapter will read
`TRADERNET_PUBLIC_KEY` and `TRADERNET_PRIVATE_KEY` and pass them explicitly to
the SDK's `public` and `private` constructor parameters; the SDK does not read
these environment variable names automatically. Other API keys can still be
configured using the existing environment or `.env` setup.

### CLI Usage

Launch the interactive CLI:
```bash
tradingagents          # installed command
python -m cli.main     # alternative: run directly from source
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more. Your previous run's answers come back as the defaults, so pressing Enter accepts them. The `TRADINGAGENTS_*` variables in `.env` still skip their step entirely.

### Non-interactive CLI

Run a predefined ticker without any input prompts, using models, providers, URLs,
language, and debate settings from the environment and configured defaults:

```bash
uv run --env-file .env tradingagents --ticker FRHC --non-interactive --checkpoint
```

This defaults to today's local date and all four analysts (`market`, `social`,
`news`, `fundamentals`). To fix the date for repeatable runs or checkpoint resume:

```bash
uv run --env-file .env tradingagents \
  --ticker FRHC --date 2026-09-10 --non-interactive --checkpoint
```

To select a subset, repeat `--analyst`, for example
`--analyst market --analyst news`. Supplying ticker, date, or analysts without
`--non-interactive` skips only those questions and retains the other interactive
choices. Non-interactive mode requires `--ticker`; invalid inputs or missing
required provider keys fail rather than prompting or writing credentials.

Full reports are saved automatically to
`<results_dir>/<ticker>/<date>/reports/complete_report.md`, alongside the individual
stage reports. The default root is `~/.tradingagents/logs`; override it with
`TRADINGAGENTS_RESULTS_DIR`. Repeating a ticker/date updates reports in the same
directory. The command prints the decision and report path without asking to save
or display the full report. Report-save failures produce a nonzero exit status.

Add `--silent` to suppress execution stdout/stderr, including the live display,
final summary, Python warnings, and standard logging (including file handlers).
Reports and `message_tool.log` are still saved, and exit codes are preserved.
`--silent` requires `--non-interactive`; using it alone exits with code 2 without
prompting or printing an error. It does not implicitly change the execution mode.
Help and argument-parser errors (such as unknown options or invalid analyst names)
remain visible, as does any output during imports before command execution.
Native/subprocess writes that bypass Python stdout/stderr are not redirected.

```bash
uv run --env-file .env tradingagents --ticker FRHC --non-interactive --silent --checkpoint
```

Checkpoint resume requires the same date, analyst selection, discussion settings,
and cache location. Fix `--date` when resuming on a later day. Checkpointing remains
controlled by `--checkpoint` / `--no-checkpoint` or
`TRADINGAGENTS_CHECKPOINT_ENABLED` when neither flag is supplied.

Exported environment variables can take precedence over `.env`. To use the file's
OpenAI key instead of an inherited `OPENAI_API_KEY`, launch with:

```bash
env -u OPENAI_API_KEY uv run --env-file .env tradingagents \
  --ticker FRHC --non-interactive --checkpoint
```

### Markets and tickers

TradingAgents works with any market Yahoo Finance covers, using the exchange-suffixed ticker. Company identity and the alpha benchmark resolve automatically per market.

- US: `AAPL`, `SPY`
- Hong Kong: `0700.HK` · Tokyo: `7203.T` · London: `AZN.L`
- India: `RELIANCE.NS`, `.BO` · Canada: `.TO` · Australia: `.AX`
- China A-shares: Shanghai `.SS`, Shenzhen `.SZ` (e.g. `600519.SS` for Kweichow Moutai)
- Crypto: `BTC-USD`, `ETH-USD`

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## TradingAgents Package

### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen (Alibaba DashScope, international and China endpoints), GLM (Zhipu), MiniMax (global + China), OpenRouter, Ollama for local models, and Azure OpenAI for enterprise.

### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-09-01")
print(decision)
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # e.g. openai, google, anthropic, deepseek, groq, ollama; openai_compatible covers any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp, ...)
config["deep_think_llm"] = "gpt-6-sol"    # Model for complex reasoning
config["quick_think_llm"] = "gpt-6-luna"   # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-09-01")
print(decision)
```

See `tradingagents/default_config.py` for all configuration options.

### Fundamentals as filed

US company statements can come from SEC EDGAR, which records the date every figure was filed. A run dated in the past then reads the statements exactly as they stood that day: a fiscal year that has ended but has not been filed yet is not served, and a figure restated later still reads as first reported. Apple's 2008 total assets were filed as $39.6B and restated to $36.2B in 2010, so a run dated in between reads $39.6B.

EDGAR needs no account or API key. Add the vendor to the chain:

```python
config["data_vendors"]["fundamental_data"] = "sec_edgar,yfinance"
```

SEC asks callers to identify themselves and refuses requests that carry no contact address, so a default one is sent. Set your own so SEC can reach you rather than the project:

```bash
SEC_EDGAR_USER_AGENT="Your Name your@email.com"
```

It covers companies that file with the SEC, including foreign companies listed in the US. Anything else, such as Hong Kong or A-share listings, falls through to the next vendor in the chain. EDGAR's machine-readable filings begin in 2009, and a fourth quarter is reported as unavailable rather than derived, because filers publish it only inside the annual figure.

### Current holdings

By default the agents do not know what you hold, so their guidance is written for a reader who applies it to their own position. Pass a portfolio to have the trader, the risk analysts and the portfolio manager work against your actual book.

```python
from tradingagents.portfolio import PortfolioContext

portfolio = PortfolioContext.model_validate({
    "cash": 25000.0,
    "currency": "USD",
    "positions": [{"ticker": "NVDA", "quantity": 120, "average_price": 150.0}],
})
_, decision = ta.propagate("NVDA", "2026-09-01", portfolio=portfolio)
```

The CLI takes the same content as a JSON file: `tradingagents --portfolio my_book.json`.

An empty `positions` list means a flat book, which is different from passing nothing. A run without a portfolio is never treated as flat.

### Per-model provider and URL

Deep and quick can independently select a provider and URL while retaining the existing model keys. These four optional Python keys all default to `None`; nonempty ENV values override their corresponding keys:

| Python | ENV |
| --- | --- |
| `deep_think_llm_provider` | `TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER` |
| `quick_think_llm_provider` | `TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER` |
| `deep_think_llm_backend_url` | `TRADINGAGENTS_DEEP_THINK_LLM_BACKEND_URL` |
| `quick_think_llm_backend_url` | `TRADINGAGENTS_QUICK_THINK_LLM_BACKEND_URL` |

For each tier, provider and URL resolve **independently**: use the nonempty individual provider, otherwise `llm_provider`; use the nonempty individual URL, otherwise `backend_url`. Missing keys, `None`, and empty strings inherit. **The common URL is inherited even when the individual provider differs**: there is no compatibility guard or automatic URL repair. An individual URL alone retains the common provider. If neither URL is set, the selected adapter keeps its existing defaults, provider ENV resolution (such as `OLLAMA_BASE_URL`), and mandatory-URL errors (`openai_compatible` requires a URL). Configurations without these overrides retain their connection behavior.

For example, uncomment these illustrative settings in your own environment configuration for a gateway serving both protocols:

```dotenv
# TRADINGAGENTS_LLM_PROVIDER=openai
# TRADINGAGENTS_DEEP_THINK_LLM=gpt-6-sol
# TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER=openai
# TRADINGAGENTS_DEEP_THINK_LLM_BACKEND_URL=http://localhost:8317/v1
# TRADINGAGENTS_QUICK_THINK_LLM=gemini-3.8-flash-high
# TRADINGAGENTS_QUICK_THINK_LLM_PROVIDER=google
# TRADINGAGENTS_QUICK_THINK_LLM_BACKEND_URL=http://localhost:8317/v1beta
```

`gemini-3.8-flash-high` is the direct model ID sent to the provider, not a new application alias. Explicit URLs matter for mixed protocols: with common OpenAI URL `http://localhost:8317/v1` and only a quick provider override to Google, quick would inherit `/v1`, not automatically switch to `/v1beta`. Both tiers may also use the same provider with different individual URLs. Model settings and agent assignments are otherwise unchanged.

**Authorization remains provider-specific and shared, not per model.** The mixed example uses the existing `OPENAI_API_KEY` and `GOOGLE_API_KEY` mechanisms. Other existing key ENV names are `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY` / `DASHSCOPE_CN_API_KEY`, `ZHIPU_API_KEY` / `ZHIPU_CN_API_KEY`, `MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `MOONSHOT_API_KEY` (Kimi), `GROQ_API_KEY`, and `NVIDIA_API_KEY`. Generic `openai_compatible` uses optional `OPENAI_COMPATIBLE_API_KEY`; Ollama needs no key. Two endpoints of the same provider retain that provider's shared authorization mechanism; there are no individual per-model authorization keys. A local URL does not make the OpenAI or Google Python client keyless. TradingAgents does not read OpenCode configuration, and an OpenCode key value `none` establishes no keyless Python-client support.

Azure keeps `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT_NAME`, and `OPENAI_API_VERSION`. Bedrock keeps `AWS_BEARER_TOKEN_BEDROCK` (taking precedence) or the AWS credential chain, including optional `AWS_PROFILE`, with `AWS_REGION` / `AWS_DEFAULT_REGION` for region selection. **Azure and Bedrock keep their existing adapter endpoint and authorization mechanisms; these overrides do not add generic base-URL support to either adapter.**

The CLI preserves individual connections, uses each tier's actual provider for model selection, and checks each unique actual provider's authorization, not an unused common provider. No per-tier connection menus are added. Existing common selection and ENV skip rules remain; individual connection overrides alone do not necessarily skip the common selection.

### Per-tier reasoning effort

Deep and quick can use independent reasoning settings without changing their models. The built-in model defaults remain `gpt-6-sol` for deep and `gpt-6-luna` for quick. Reasoning settings do not change model selection, agent assignments, providers or endpoints, or other generation settings.

| Tier | Environment variable | Python key | Built-in default |
| --- | --- | --- | --- |
| Deep | `TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT` | `deep_think_reasoning_effort` | `None` (inherit) |
| Quick | `TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT` | `quick_think_reasoning_effort` | `None` (inherit) |

For example, set these in `.env` (uncomment the corresponding lines in `.env.example`):

```dotenv
# Illustrative values, not defaults; check support for your provider/model.
# TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT=high
# TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT=low
```

Or configure the two keys independently in Python before constructing the graph:

```python
config = DEFAULT_CONFIG.copy()
# Illustrative values, not defaults; check support for your provider/model.
config["deep_think_reasoning_effort"] = "high"
config["quick_think_reasoning_effort"] = "low"
```

Reasoning is resolved **independently for each tier**, in this order:

1. The tier's nonempty setting.
2. The nonempty shared setting for that tier's **actual provider** (see below). Shared settings for other providers do not apply.
3. Omit the reasoning parameter from the request so provider defaults apply.

Missing keys, `None`, and empty strings (`""`, or an empty environment value) mean **inheritance**, not disabling reasoning. The literal string `"none"` is not an inheritance sentinel; its support depends on the provider/model. Both new Python defaults are `None`, and the environment template leaves the illustrative overrides commented out.

For a partial override, `deep_think_reasoning_effort="high"`, `openai_reasoning_effort="medium"`, and quick unset yield deep=`high` and quick=`medium` with OpenAI. If neither a tier setting nor the active provider's shared setting is set, no reasoning parameter is sent for that tier. Existing configurations without tier overrides continue to use the shared setting.

| Active provider | Shared fallback Python key | Shared environment variable | Adapter parameter |
| --- | --- | --- | --- |
| OpenAI, existing OpenAI-compatible providers, Azure | `openai_reasoning_effort` | `TRADINGAGENTS_OPENAI_REASONING_EFFORT` | `reasoning_effort` |
| Anthropic | `anthropic_effort` | `TRADINGAGENTS_ANTHROPIC_EFFORT` | `effort` |
| Google | `google_thinking_level` | `TRADINGAGENTS_GOOGLE_THINKING_LEVEL` | `thinking_level` |

For a mixed pair with no tier reasoning overrides, OpenAI deep inherits `openai_reasoning_effort`, while Google quick inherits `google_thinking_level`. For example, shared `high` and `minimal` respectively produce `reasoning_effort=high` and `thinking_level=minimal`. No new Bedrock reasoning protocol is introduced.

**Reasoning compatibility change:** outgoing reasoning is no longer filtered by model name or rewritten. Nonempty values, including literal `none` and Google's `minimal` (also for Pro models), pass unchanged at the application boundary. There is no universal enum: **`high` and `low` are examples, not defaults or a promise of SDK/server acceptance**. Errors are not hidden by retrying without reasoning. This also affects old common settings that were previously silently filtered or transformed. To omit a rejected parameter in graph configuration, remove both the individual value and its provider's common fallback. Omission uses provider defaults; it is not a universal server-side reasoning-off switch.

The graph's nonempty resolution above is distinct from direct OpenAI adapter calls: explicitly supplied `reasoning_effort=None` or `reasoning_effort=""` still passes to the SDK boundary; an absent kwarg is not added. Response normalization, DeepSeek's `reasoning_content` round-trip, and MiniMax's reasoning/text separation remain unchanged.

In the CLI, the existing shared interactive reasoning choice remains a fallback only for tiers using the corresponding actual provider; tier overrides and other providers' common settings from configuration survive it. There are no new per-tier menus. Existing prompt-skip rules remain: setting a nonempty `TRADINGAGENTS_LLM_PROVIDER` or the active provider's shared reasoning environment variable skips the shared prompt. Setting both tier overrides alone does **not** suppress that prompt.

## Persistence and Recovery

TradingAgents persists two kinds of state across runs.

### Decision log

The decision log is always on. Each completed run appends its decision to `~/.tradingagents/memory/trading_memory.md`. On the next run for the same ticker, TradingAgents fetches the realised return (raw, and alpha against the instrument's regional benchmark), generates a one-paragraph reflection, and injects the most recent same-ticker decisions plus recent cross-ticker lessons into the Portfolio Manager prompt, so each analysis carries forward what worked and what didn't.

Override the path with `TRADINGAGENTS_MEMORY_LOG_PATH`.

### Checkpoint resume

Checkpoint resume is opt-in via `--checkpoint`. When enabled, LangGraph saves state after each node so a crashed or interrupted run resumes from the last successful step instead of starting over. The run view says whether it resumed a saved run or started fresh. Checkpoints are cleared automatically on successful completion.

Per-ticker SQLite databases live at `~/.tradingagents/cache/checkpoints/<TICKER>.db` (override the base with `TRADINGAGENTS_CACHE_DIR`). Use `--clear-checkpoints` to reset all of them before a run.

```bash
tradingagents --checkpoint           # enable for this run
tradingagents --clear-checkpoints    # reset before running
```

```python
config = DEFAULT_CONFIG.copy()
config["checkpoint_enabled"] = True
ta = TradingAgentsGraph(config=config)
_, decision = ta.propagate("NVDA", "2026-09-01")
```

## Evaluating decisions over time

One run gives one decision, which cannot tell you whether the system decides well. `run_backtest` runs the same pipeline over a grid of tickers and dates, writes to a decision log of its own, and scores the decisions whose holding window has since traded.

```python
from tradingagents.backtest import iter_grid, run_backtest, summarize

dates = iter_grid("2026-06-01", "2026-08-01", every_n_days=7)
result = run_backtest(["NVDA", "AAPL"], dates, config, selected_analysts=["market", "news"])
print(summarize(result).render())
```

From the CLI:

```bash
tradingagents backtest NVDA,AAPL --start 2026-06-01 --end 2026-08-01 --every 7
```

Each cell is scored on realized alpha against the instrument's regional benchmark, grouped by rating. Your own decision log is never written to, and re-running the same grid with `run_id=result.run_id` skips the cells that already ran, so an interrupted sweep continues where it stopped.

## Reproducibility

TradingAgents is LLM-driven, so two runs of the same ticker and date can differ. This is expected for a research tool built on language models, not a defect. The variation comes from a few distinct sources, and it helps to separate them.

Language model sampling is non-deterministic. Even at a fixed temperature, providers do not guarantee byte-identical output across calls, and reasoning models (the default GPT-6 family, and any thinking-mode model) vary the most because their internal reasoning is itself sampled.

Live data moves. News, StockTwits, and Reddit return different content as time passes, so a run today sees different inputs than a run last week even for the same historical trade date. Pin the analysis date to hold the price and indicator window fixed, but the social and news sources still reflect "now".

To reduce variation you can lower the sampling temperature. Set `temperature` in your config (or `TRADINGAGENTS_TEMPERATURE` in `.env`); lower values make models that honor it more repeatable. The current curated models are reasoning-first and largely ignore temperature, so for tighter reproducibility name a non-reasoning model in your config, or in `TRADINGAGENTS_DEEP_THINK_LLM` and `TRADINGAGENTS_QUICK_THINK_LLM`. Any model ID your provider serves is accepted, whether or not the picker lists it.

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["temperature"] = 0.0
# Reasoning models ignore temperature. For tighter reproducibility, name a
# non-reasoning model in deep_think_llm / quick_think_llm.
```

What does not vary anymore: the analyzed company identity is resolved deterministically from the ticker before any agent runs, and the market analyst grounds exact price and indicator claims in a verified data snapshot. Earlier reports of "different companies" or fabricated price levels across runs are addressed by these two mechanisms.

Backtest results are not guaranteed to match any published figure. Returns depend on the model, the temperature, the date range, data quality, and the sampling above. Treat the framework as a research scaffold for studying multi-agent analysis, not as a strategy with a fixed, replicable return.

## Contributing

Contributions are welcome: bug fixes, documentation, and feature ideas; past contributions are credited per release in [`CHANGELOG.md`](CHANGELOG.md).

## Citation

Please reference our work if you find *TradingAgents* provides you with some help :)

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework}, 
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}, 
}
```
