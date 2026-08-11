## 1. Dependency And Configuration Foundation

- [ ] 1.1 Add a `tradernet` optional dependency extra for `tradernet-sdk` and verify research-only imports still work without the extra.
- [ ] 1.2 Define validated non-secret configuration for account scope, watchlist, rating weights, hard risk limits, initial stop policy, break-even costs/buffer, profit ladder, timeouts, cooldown, reconciliation, database path, and dry-run/live gates.
- [ ] 1.3 Add environment parsing for Tradernet keys and structured JSON settings, keep private credentials outside the effective configuration fingerprint, and fail clearly on malformed values.
- [ ] 1.4 Update non-secret environment examples and operator documentation without including usable credentials or live numeric trading defaults.

## 2. Broker Domain And Tradernet Adapter

- [ ] 2.1 Define broker-neutral validated models for balances, signed positions, executable quotes, market state, orders, fills, stop state, order intents, and classified broker failures using exact monetary representations.
- [ ] 2.2 Implement Tradernet API-key construction and diagnostic redaction, including tests proving private keys, signatures, and configured sensitive identifiers cannot enter logs or persisted payloads.
- [ ] 2.3 Parse portfolio, quote, market-status, active-order, historical-order, and fill responses into domain models and reject incomplete or contradictory payloads.
- [ ] 2.4 Implement current-time candle requests and other time-dependent reads with explicit per-invocation date ranges rather than SDK default dates.
- [ ] 2.5 Implement bounded read retries, timeout policy, and error classification for `error`, `errorMsg`, `errMsg`, transport, HTTP, authorization, validation, rejection, and unknown outcomes.
- [ ] 2.6 Implement explicit market/limit order placement and specific cancellation with signed quantity, margin policy, duration, and caller order identity; prevent reduce intents from crossing through zero after stale broker state.
- [ ] 2.7 Implement static stop and broker-native trailing operations followed by active-order reconciliation of symbol, side, quantity, status, stop price, and trailing percentage.
- [ ] 2.8 Implement quote, portfolio, order, and market-status subscriptions with bounded reconnect hooks and injectable REST reconciliation.
- [ ] 2.9 Add sanitized Tradernet fixtures and adapter contract tests for long/short positions, partial fills, all order statuses, malformed fields, application errors, and unknown request outcomes.

## 3. Operational Persistence And Reconciliation

- [ ] 3.1 Add a versioned SQLite operational schema with foreign keys and WAL for configurations, cycle runs, snapshots, intents, broker operations, fills, protection states, transitions, leases, and alerts.
- [ ] 3.2 Implement transactional repositories that preserve immutable audit inputs/responses, redact secrets, and update confirmed state without using LangGraph checkpoint or decision-memory storage.
- [ ] 3.3 Implement account-scoped leases and heartbeat expiry so only one daily cycle and one protection supervisor can own their respective work at a time.
- [ ] 3.4 Implement stable cycle, position-incarnation, order-intent, and stop-transition identities plus configuration fingerprints for idempotent restart behavior.
- [ ] 3.5 Implement authoritative reconciliation across persisted intents, Tradernet positions, active/history orders, and fills, including blocked states for unexplained discrepancies.
- [ ] 3.6 Add database migration, concurrent-owner, interrupted-transaction, unknown-outcome, and restart recovery tests.

## 4. Target And Risk Policy

- [ ] 4.1 Build the daily universe as the de-duplicated union of every open broker position and every valid configured watchlist symbol.
- [ ] 4.2 Add market-time and completed-candle guards that defer symbol decisions when required daily data is still forming, stale, or unavailable.
- [ ] 4.3 Implement the deterministic five-tier rating-to-weight mapping, preserving existing exposure for held Hold ratings and remaining flat for unheld Hold candidates.
- [ ] 4.4 Convert target weights and reconciled equity/quotes into explicit open, expand, reduce, close, and reverse long/short intents with valid lot and tick rounding.
- [ ] 4.5 Implement deterministic pre-trade checks for signed position weight, gross/net exposure, cash or margin, order notional, concentration, liquidity, permitted instruments, stale data, market state, and conflicting orders.
- [ ] 4.6 Add policy tests covering malformed research output, held versus unheld Hold, long reduction versus short entry, short reduction versus long entry, clipped limits, and refusal on missing live risk configuration.

## 5. Daily Portfolio Cycle

- [ ] 5.1 Implement an idempotent one-shot cycle that reconciles the account, records immutable inputs, runs TradingAgents once per universe symbol, and persists ratings and report references.
- [ ] 5.2 Implement dry-run planning as the default, including proposed orders, stop actions, risk decisions, expected transitions, and proof that no state-changing broker method was called.
- [ ] 5.3 Implement the two-part live readiness gate for credentials, optional dependency, configuration, database ownership, broker connectivity, reconciliation, market data, hard risk, and stop compatibility.
- [ ] 5.4 Implement serialized live execution with pre/post reconciliation and terminal/unknown handling for each order rather than assuming submission equals execution.
- [ ] 5.5 Implement close-then-reconcile-then-open sequencing for long/short reversals and recalculate all subsequent work from partial fills.
- [ ] 5.6 Attach and confirm initial static protection immediately after every new or expanded fill, halting later exposure increases and raising an alert if any filled quantity remains unprotected.
- [ ] 5.7 Add cycle tests for duplicate invocation, interrupted resume, partial fills, failed initial protection, reversals, unknown outcomes, and immutable audit reconstruction.

## 6. Profit-Protection Policy

- [ ] 6.1 Implement strict validation for initial protection, break-even activation, cost/slippage assumptions, buffer, increasing profit thresholds, non-increasing trailing gaps, minimum improvement, tick size, cooldown, and timing limits.
- [ ] 6.2 Implement economic break-even calculations for long and short positions using weighted fills, actual or conservative costs, slippage, buffer, and protective tick rounding.
- [ ] 6.3 Implement durable favorable watermarks using bid for long and ask for short, with explicit blocking behavior when executable-side quotes are unavailable or stale.
- [ ] 6.4 Implement the protection state machine from initial protection through break-even, trailing, exit, closed, and error states with validated transition preconditions.
- [ ] 6.5 Implement profit-ladder selection that retains the highest reached level and permits only stop/gap updates that monotonically improve long or short protection.
- [ ] 6.6 Implement cooldown, rounded minimum-improvement, and transition-id checks that suppress duplicate or immaterial Tradernet stop updates.
- [ ] 6.7 Recalculate and reconcile protection after partial fills, reductions, closures, and reversals without carrying state into a new signed position incarnation.
- [ ] 6.8 Add exhaustive policy tests for long/short symmetry, break-even costs, rounding, watermark persistence, ladder progression, no downgrade, no weakened stop, and gap-through fill accounting.

## 7. Continuous Protection Supervisor

- [ ] 7.1 Implement an account-leased always-on supervisor that starts by reconciling every open position and confirmed broker-held stop before processing transitions.
- [ ] 7.2 Consume quote, portfolio, order, and market events, pause transitions during reconnect, restore subscriptions, and reconcile before resuming.
- [ ] 7.3 Add periodic REST reconciliation and configured quote polling fallback while ensuring stale data can block transitions but cannot remove existing broker protection.
- [ ] 7.4 Implement break-even replacement followed by confirmed broker-native trailing activation and later profit-ladder gap tightening.
- [ ] 7.5 Detect a reset trailing anchor, weakened stop, ambiguous symbol/account ownership, rejected update, or local/broker mismatch; preserve or restore the safest confirmed protection and enter an alerted error state.
- [ ] 7.6 Add supervisor tests for process restart, lease takeover, stream disconnect, missed events, polling fallback, monitor outage with broker stop retained, and state disagreement.

## 8. Commands And Operations

- [ ] 8.1 Add non-interactive commands for read-only Tradernet reconciliation, the one-shot portfolio cycle, and the continuous protection supervisor with clear optional-extra guidance.
- [ ] 8.2 Require an explicit live confirmation flag or service environment switch in addition to persistent live configuration, and make command output unmistakably identify dry-run versus live mode.
- [ ] 8.3 Add inspectable cycle, position-protection, pending-unknown, and alert status output that does not expose credentials.
- [ ] 8.4 Document external cron/systemd scheduling, supervisor restart behavior, operational database backup, live disablement, and rollback that leaves broker-held stops intact.
- [ ] 8.5 Add trusted-environment read-only smoke tooling for account summary, active orders, market status, quotes, and response-shape capture outside normal CI.
- [ ] 8.6 Add separately gated minimal-size compatibility tooling for static stop, break-even replacement, native long/short trailing, repeated gap tightening, partial fills, cancellation, and restart; require explicit symbol and quantity.

## 9. Verification And Rollout Gates

- [ ] 9.1 Run focused broker, persistence, policy, daily-cycle, and supervisor tests and resolve all failures.
- [ ] 9.2 Run `pytest -q` and `ruff check .` across the complete repository.
- [ ] 9.3 Verify clean package installation and imports both without extras and with the `tradernet` extra on supported Python versions.
- [ ] 9.4 Complete a sustained dry-run/observation soak using real portfolio and quote reads and review generated plans, reconciliation results, and alerts.
- [ ] 9.5 In a trusted account, record successful explicitly gated long and short stop-compatibility results, including whether changing trailing percentage preserves the anchor and monotonic protection.
- [ ] 9.6 Keep live readiness disabled until every configured risk/stop value is explicit and the dry-run, recovery, and Tradernet compatibility evidence is recorded.
