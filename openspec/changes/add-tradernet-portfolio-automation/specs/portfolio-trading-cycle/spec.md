## Purpose

Defines an auditable daily portfolio-management cycle that combines TradingAgents research with real holdings and a configured watchlist while keeping execution deterministic, idempotent, and dry-run by default.

## ADDED Requirements

### Requirement: Portfolio and watchlist universe
Each daily cycle SHALL include every currently open position and every valid symbol in the configured watchlist, with duplicate symbols analyzed only once.

#### Scenario: Holding is absent from watchlist
- **WHEN** the broker portfolio contains a position not present in the configured watchlist
- **THEN** the daily cycle SHALL still analyze and manage that position

#### Scenario: Watchlist symbol has no position
- **WHEN** a valid watchlist symbol is not currently held
- **THEN** the daily cycle SHALL include it as a candidate for a new long or short target

### Requirement: Closed-data daily cadence
The daily cycle SHALL use a configured market timezone and only completed daily market data for the intended trading date.

#### Scenario: Daily candle is incomplete
- **WHEN** the latest candle is still forming or the market session required by the configured policy has not closed
- **THEN** the cycle SHALL defer decisions that depend on that candle
- **AND** SHALL record why the cycle did not produce executable intents

### Requirement: Idempotent cycle identity
The system SHALL assign each daily cycle a stable identity derived from account scope, strategy configuration, and trading date, and SHALL prevent duplicate execution for that identity.

#### Scenario: Completed cycle is started again
- **WHEN** a completed cycle is invoked again with the same identity
- **THEN** the system SHALL return the recorded result without submitting duplicate orders

#### Scenario: Interrupted cycle resumes
- **WHEN** a cycle restarts after partial progress
- **THEN** it SHALL reconcile recorded actions with broker state before continuing from a safe stage

### Requirement: Signed target exposure
The system SHALL convert research output through a deterministic policy into an explicit signed target exposure where positive values represent long exposure, negative values represent short exposure, and zero represents no position.

#### Scenario: Sell analysis for an existing long
- **WHEN** research and configured policy produce a zero target for an existing long position
- **THEN** the resulting intent SHALL close or reduce the long position rather than implicitly open a short

#### Scenario: Short target is selected
- **WHEN** policy explicitly produces a negative target within configured limits
- **THEN** the resulting intent SHALL identify short entry or expansion separately from long reduction

#### Scenario: Research output is ambiguous
- **WHEN** required research output cannot be parsed or mapped deterministically
- **THEN** the system SHALL produce no exposure-increasing order for that symbol

### Requirement: Deterministic risk controls
Every executable intent SHALL pass configured position, portfolio, cash/margin, concentration, liquidity, and order-size controls independently of LLM recommendations.

#### Scenario: Intent exceeds a hard limit
- **WHEN** a proposed target violates any configured hard risk limit
- **THEN** the system SHALL reject or reduce the target according to the configured deterministic policy
- **AND** SHALL record the violated limit and resulting action

#### Scenario: Required risk configuration is missing
- **WHEN** live execution starts without complete valid hard-risk configuration
- **THEN** live execution SHALL be refused

### Requirement: Dry-run by default
The system SHALL default to dry-run mode, in which it reads real portfolio and market data and creates a complete execution plan without submitting, changing, or cancelling broker orders.

#### Scenario: Dry-run cycle completes
- **WHEN** a cycle runs without explicit live enablement
- **THEN** it SHALL persist proposed orders, stop actions, risk decisions, and expected state transitions
- **AND** Tradernet state SHALL remain unchanged

### Requirement: Explicitly gated live execution
Live execution SHALL require explicit runtime enablement and a previously successful readiness check covering credentials, portfolio reconciliation, risk configuration, stop policy, and broker connectivity.

#### Scenario: Live mode lacks readiness
- **WHEN** live mode is requested and any readiness check fails
- **THEN** the cycle SHALL stop before its first state-changing broker request

### Requirement: Sequenced position transitions
The system SHALL reconcile fills between reduction, closure, reversal, and expansion stages and SHALL NOT assume that a submitted order has executed.

#### Scenario: Reverse from long to short
- **WHEN** the approved target changes from positive to negative exposure
- **THEN** the system SHALL close the long, confirm the resulting broker position and old-stop disposition, and only then submit the short entry

#### Scenario: Entry partially fills
- **WHEN** an entry order partially fills
- **THEN** subsequent protection and sizing SHALL use the confirmed filled quantity rather than the originally requested quantity

### Requirement: Immediate initial protection
Every newly filled or expanded position SHALL receive a confirmed broker-held initial protective stop before the cycle can open another exposure-increasing position.

#### Scenario: Initial stop cannot be confirmed
- **WHEN** a filled position does not receive a valid active protective stop within configured limits
- **THEN** the cycle SHALL halt additional exposure increases
- **AND** SHALL raise an operational alert with the unprotected quantity

### Requirement: Auditable cycle result
The cycle SHALL persist immutable inputs, normalized broker snapshots, research references, target exposures, risk decisions, broker requests and responses, fills, stop confirmations, and final reconciliation without storing credentials.

#### Scenario: Operator reviews a cycle
- **WHEN** an operator opens a completed or failed cycle record
- **THEN** the record SHALL explain how each final position and order resulted from its inputs and safeguards
