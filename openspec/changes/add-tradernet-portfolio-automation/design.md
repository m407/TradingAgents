## Context

See `proposal.md` for motivation. The existing LangGraph workflow analyzes one ticker and persists research-oriented Markdown, JSON, memory, and optional checkpoints. It has no account ledger, broker abstraction, order lifecycle, scheduler, or process-safe operational store. Structured agent outputs are rendered back to Markdown, and malformed final output can conservatively become Hold; that availability behavior is unsuitable as an execution contract.

Tradernet exposes the required portfolio, order, stop, quote, and event APIs, and `tradernet-sdk` wraps the critical subset. The SDK returns unvalidated dictionaries, does not raise all documented application errors, lacks bounded reconnect/retry behavior, contains import-time date defaults, and exposes an order API whose margin-oriented defaults must not leak into strategy behavior. Tradernet remains the authority for actual positions, orders, and fills.

The change spans a daily analysis process and an always-on protection process. They may overlap and therefore require transactional state, single-owner coordination, deterministic idempotency, and reconciliation rather than the existing report and memory files.

## Goals / Non-Goals

**Goals:**

- Isolate all broker side effects behind a validated project-owned boundary.
- Preserve the existing analysis graph while adding deterministic conversion from ratings to signed long/short targets.
- Make dry-run the default and require an explicit readiness gate for live operation.
- Ensure a newly filled position receives broker-held protection and remains protected if application processes fail.
- Move protection to economic break-even and then tighten broker-native trailing through an explicit profit ladder.
- Recover daily-cycle and protection state after crashes without duplicating orders or weakening stops.
- Produce an immutable operational audit trail that excludes secrets.

**Non-Goals:**

- High-frequency or intraday alpha generation.
- Tick-level backtesting, exchange simulation, smart order routing, or guaranteed stop execution price.
- Managing more than one broker implementation in the first delivery, although the boundary remains broker-neutral.
- Automatically selecting profit thresholds, risk limits, watchlists, or position sizes for the operator.
- Replacing TradingAgents market-data vendors or analytical report formats with Tradernet data.
- Exposing live trading through the existing interactive analysis flow.

## Decisions

### Keep broker operations outside LangGraph

Add an operational portfolio package alongside the current graph rather than broker nodes inside it. The daily orchestrator invokes `TradingAgentsGraph.propagate()` per symbol, reads its normalized final rating, and then applies a deterministic target and risk policy. The protection supervisor never calls an LLM.

This prevents graph retries, free-text fallback, and checkpoint resume from replaying financial side effects. It also lets stop protection continue while expensive daily analysis is not running. Embedding order nodes in LangGraph was rejected because its current state and persistence are research-oriented and not transactional.

### Introduce a broker protocol and Tradernet adapter

Define project-owned models and operations for account snapshots, signed positions, quotes, orders, fills, market state, placement, cancellation, and stop management. The Tradernet adapter wraps `tradernet-sdk` for supported calls and uses its authenticated request primitive only where the high-level SDK omits required API fields.

The adapter will:

- parse raw responses into validated models using `Decimal` for prices, money, and quantities where the broker permits them;
- evaluate request timestamps and date ranges at call time;
- classify HTTP, transport, authentication, validation, rejection, and unknown-outcome failures;
- retry only idempotent reads automatically;
- require an idempotency identity and reconciliation for state-changing requests;
- override SDK margin defaults with explicit intent;
- redact keys, signatures, account identifiers where configured, and sensitive response fields from logs.

Using raw Tradernet HTTP throughout was rejected because the SDK already supplies signing and the critical method names. Using the SDK directly throughout was rejected because its `Any` responses and error handling cannot enforce execution invariants.

Package `tradernet-sdk` as an optional `tradernet` project extra so research-only installations remain unchanged. Tradernet commands provide a clear installation error when the extra is absent.

### Use one transactional operational database

Store operational records in a dedicated SQLite database under the configured TradingAgents data directory, separate from LangGraph checkpoints and Markdown decision memory. Enable foreign keys and WAL mode, use short transactions, and acquire a lease for each account-scoped daily cycle and monitor owner.

Core records cover:

- strategy configuration fingerprint and run identity;
- immutable broker snapshots and daily-cycle inputs/outputs;
- order intents, broker order identities, requests, responses, fills, and reconciliation results;
- one protection state per account, symbol, and signed position incarnation;
- high/low watermark, economic break-even, highest ladder level, confirmed stop, and transition history;
- monitor leases, alerts, and unknown-outcome work awaiting reconciliation.

Tradernet is authoritative for live positions, orders, and fills. SQLite is authoritative for strategy intent, idempotency, reached ladder state, and audit history. Any unexplained disagreement blocks automation for the affected position.

Reusing checkpoint SQLite was rejected because checkpoints are cleared on success, are partitioned by ticker, and do not provide an operational transaction or audit model.

### Split daily orchestration from continuous supervision

Expose two non-interactive commands:

- a one-shot daily portfolio cycle intended for an external cron or systemd timer after the configured market close;
- an always-on protection monitor intended to run as a supervised service.

The daily process reconciles the account, analyzes the union of open positions and configured watchlist, creates target exposures, performs risk checks, and plans or executes position changes. The monitor subscribes to quotes, portfolio, orders, and market status, with periodic REST reconciliation and polling fallback.

An embedded scheduler was rejected because process supervision, restart policy, and market-time invocation are better handled by the deployment environment. A daily-only monitor was rejected because it cannot protect an intraday profit from a sudden news reversal.

### Represent decisions as signed target exposure

The execution boundary consumes target exposure, not Buy/Sell verbs. Positive exposure is long, negative exposure is short, and zero is flat. A configuration-defined mapping converts the five-tier final rating into target portfolio weights. For an existing position, Hold preserves current exposure; for an unheld watchlist candidate, Hold remains flat. Exact Buy, Overweight, Underweight, and Sell weights are mandatory non-secret configuration.

The policy calculates a target quantity from reconciled equity, quote, lot size, and risk limits, then produces an explicit intent such as reduce-long, close-long, open-short, reduce-short, close-short, or open-long. Reversals close and reconcile the old side before opening the new side. This removes the ambiguity where Sell could mean either reducing a long or opening a short.

### Apply deterministic risk gates after research

The LLM rating is advisory. A deterministic layer validates maximum signed position weight, gross and net exposure, cash/margin availability, per-order notional, concentration, liquidity, permitted-instrument state, stale data, market state, and outstanding conflicting orders. Invalid structured output and missing evidence cannot increase exposure.

The first implementation uses configuration-defined limits rather than inventing trading defaults. Missing live limits fail readiness. Dry-run records rejected and clipped targets so settings can be tuned before live enablement.

### Make live mode a two-part gate

Dry-run is the configuration default. Live operation requires both a persistent explicit mode setting and a per-invocation confirmation flag or service environment switch. Startup then runs a readiness check covering credentials, SDK availability, account and order reconciliation, configuration validation, market data freshness, stop compatibility, and operational database ownership.

This two-part gate reduces accidental live trading caused by copying a configuration file or invoking the wrong command. There is no automatic transition from dry-run to live after a number of successful runs.

### Protect every fill before increasing further exposure

After a new or expanded fill, the daily cycle calculates an initial static stop from explicit strategy configuration, submits it, and reconciles the active broker order. It will not submit another exposure-increasing order while any newly filled quantity is unprotected. Partial fills are protected using confirmed quantity and recalculated weighted average entry.

The initial-stop model is intentionally independent of the profit ladder. The ladder controls result protection after favorable movement; it does not replace the operator's initial risk limit.

### Use an explicit profit-protection state machine

Each signed position incarnation transitions through:

1. `INITIAL_PROTECTION`: a confirmed static stop protects downside.
2. `BREAK_EVEN_READY`: executable price has crossed the configured activation threshold.
3. `BREAK_EVEN_PROTECTED`: Tradernet confirms a static stop at economic break-even or better.
4. `TRAILING`: Tradernet confirms native trailing at the highest reached ladder level.
5. `EXIT_PENDING`: the stop activated or another approved exit is executing.
6. `CLOSED` or `ERROR`: reconciliation confirmed closure, or automation halted on an invariant violation.

For a long position, the executable trigger price is bid and the favorable watermark is the maximum bid. For a short position, the trigger price is ask and the favorable watermark is the minimum ask. If only last price is available, live transition is blocked unless the operator explicitly configures that quote-quality policy.

Economic break-even includes weighted average entry, actual known charges, conservative estimates for unknown entry/exit costs, expected slippage, and a configured buffer. Values are rounded in the protective direction to the instrument tick size.

### Tighten protection monotonically through a ladder

The configured ladder has strictly increasing favorable-profit thresholds and non-increasing trailing gaps. The highest crossed level is persisted and never downgraded while the position incarnation remains open.

Before trailing activation, the supervisor first confirms the break-even static stop. It then requests broker-native trailing for the selected level and reconciles actual `stop`, status, quantity, and trailing percentage. Later levels can reduce the trailing gap only when the expected and confirmed stop preserve or improve protection.

For a long, a confirmed stop must be greater than or equal to the prior stop and economic break-even. For a short, it must be less than or equal to both. Cooldown, tick rounding, minimum stop improvement, and stable transition IDs suppress duplicate updates.

Tradernet's behavior when changing trailing percentage or `stop_init_price` is a live-readiness compatibility test. If an update resets the anchor or weakens the stop, the supervisor retains or restores the safest confirmed order, enters `ERROR`, and does not attempt the next ladder transition. The design does not emulate high-frequency trailing by repeatedly replacing static orders.

### Reconcile around every side effect

The daily cycle and supervisor use a common pattern:

1. persist intended transition and deterministic idempotency identity;
2. reconcile immediately before submission;
3. submit once;
4. persist raw sanitized response;
5. reconcile broker state until a terminal or configured timeout condition;
6. commit the confirmed transition or mark it unknown/error.

Order placement uses Tradernet's caller order identity where supported. Stop changes additionally use the local position-incarnation and transition identity because the stop API is symbol-oriented. Unknown outcomes are resolved by active-order, history, fill, and position queries before any repeat request.

### Recover streams with REST authority

WebSocket events reduce reaction time but are not assumed complete or ordered across reconnects. Each stream runs with bounded exponential reconnect, and processing pauses after reconnect until portfolio and active-order snapshots are reconciled. A periodic REST timer detects missed events. If WebSocket quotes remain unavailable, configured REST polling may continue monitoring; stale quotes block stop transitions but never remove broker-held protection.

### Separate secrets from strategy configuration

Public/private Tradernet keys are environment-only. Non-secret settings extend the existing configuration model and environment override pattern. Structured watchlist, rating-to-weight mapping, and profit-ladder values are accepted programmatically and through validated JSON environment values. Configuration fingerprints exclude secrets but include every value that affects cycle identity, risk, or stop behavior.

No live numeric defaults are provided. Dry-run can validate an explicitly supplied candidate policy, but live readiness fails if required values are absent.

### Test in layers

Unit tests use synthetic and sanitized Tradernet fixtures for response parsing, error shapes, long/short normalization, target mapping, risk gates, state transitions, rounding, monotonicity, partial fills, idempotency, and recovery. Contract tests mock transport at the adapter boundary. Process tests exercise SQLite restart and lease behavior.

Trusted-environment smoke commands are separate from pytest and start read-only: account summary, active orders, market status, and quotes. State-changing compatibility tests require a dedicated explicit flag, operator-selected symbol and minimal quantity, and are never part of CI. Dry-run soak testing precedes any live readiness approval.

## Risks / Trade-offs

- [Tradernet stop updates may reset their trailing anchor or use undocumented long/short semantics] -> Gate live trailing on explicit compatibility tests, reconcile every update, and enter an error state rather than weakening protection.
- [A market gap can execute beyond the stop trigger] -> Treat stops as triggers rather than guaranteed prices, persist actual fills and slippage, and reconcile resulting exposure.
- [A filled entry can briefly exist before its stop is confirmed] -> Serialize exposure increases, submit protection immediately after fill events, impose a strict confirmation timeout, and alert/halt on an unprotected quantity.
- [WebSocket loss can delay break-even activation] -> Keep the initial broker stop active, reconnect with backoff, use REST polling fallback, and block transitions on stale data.
- [SQLite supports limited concurrent writers] -> Keep transactions short, use WAL, acquire account-scoped leases, and allow only one active supervisor per account.
- [Per-symbol Tradernet stops may not distinguish multiple account scopes or lots] -> Include account scope in local identity, verify returned position/order ownership, and refuse ambiguous multi-position management.
- [LLM analysis is costly and non-deterministic] -> Run it only in the daily cycle, persist input/output references, make execution mapping deterministic, and treat malformed output as no exposure increase.
- [Long and short reversals can race partial fills] -> Sequence close, reconciliation, stop cleanup, and opposite-side entry; never submit both sides from one assumed fill state.
- [Conservative transaction-cost estimates can move break-even later] -> Expose assumptions in audit output and replace estimates with confirmed costs without loosening protection.
- [The optional dependency adds an extra installation mode] -> Verify clean installs both with and without the `tradernet` extra and provide command-level setup guidance.

## Migration Plan

1. Add the optional dependency, domain models, adapter, and fixture-based tests with no live command enabled.
2. Add operational schema migrations and inspectable read-only reconciliation commands.
3. Add the daily cycle in dry-run only and collect execution plans for the configured portfolio and watchlist.
4. Add the protection state machine and monitor in observation mode, comparing proposed transitions with broker state without mutations.
5. Run trusted read-only smoke tests, restart/reconnect tests, and a dry-run soak period.
6. Run explicitly gated minimal-size compatibility tests for static stop, break-even replacement, native long trailing, native short trailing, repeated gap tightening, partial fill, cancellation, and restart.
7. Enable live mode only for an explicitly configured account after all readiness evidence is recorded.

Rollback disables both live gates and stops the application processes. Existing broker-held protective orders are deliberately left in place unless an operator explicitly cancels them after reviewing the portfolio. Operational data is retained for audit, and research-only TradingAgents behavior remains available without the optional extra.
