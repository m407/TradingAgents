## Why

TradingAgents currently produces single-ticker research ratings without access to the user's real portfolio or any mechanism for safe order execution. Medium-term portfolio management through Tradernet requires a separate operational layer that reconciles broker state, converts analysis into explicit long/short targets, and protects profitable positions from sudden reversals without relying on an unattended LLM decision.

## What Changes

- Add an authenticated Tradernet brokerage integration for portfolio snapshots, quotes, orders, fills, market status, cancellation, and stop management.
- Add a daily portfolio cycle that always evaluates current positions plus a configured watchlist, produces signed long/short target exposure, applies deterministic risk controls, and supports dry-run before explicitly enabled live execution.
- Add a continuously running profit-protection monitor that begins with a broker-held protective stop, moves the stop to economic break-even after a configured profit threshold, and then manages a broker-native trailing stop through a configurable profit ladder.
- Add durable operational state, idempotency, broker reconciliation, restart recovery, audit records, and fail-closed behavior for unattended operation.
- Keep numeric stop thresholds, profit levels, trailing gaps, transaction-cost estimates, and live enablement explicit configuration rather than embedding trading defaults.

## Capabilities

### New Capabilities

- `tradernet-brokerage`: Typed and resilient access to Tradernet portfolio, market, order, fill, and stop operations.
- `portfolio-trading-cycle`: Daily dry-run/live orchestration for current holdings and a configured watchlist with explicit signed exposure and deterministic execution safeguards.
- `profit-protection`: Continuous long/short stop supervision that transitions from initial protection to economic break-even and profit-ladder trailing without weakening protection.

### Modified Capabilities

None.

## Impact

- Adds a broker-facing integration boundary, portfolio domain models, operational persistence, a daily command, and a long-running monitor alongside the existing LangGraph research workflow.
- Extends configuration and environment-variable handling for Tradernet credentials, watchlists, risk limits, stop policy, dry-run/live mode, reconciliation, and monitor behavior.
- Adds the `tradernet-sdk` dependency while wrapping its raw responses and incomplete error handling behind project-owned validation.
- Adds tests using recorded or synthetic Tradernet response fixtures; real-account verification remains read-only or explicitly gated and must not run in normal CI.
- Introduces real financial side effects only when live mode is explicitly enabled; the default remains dry-run.
