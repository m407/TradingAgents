## Purpose

Protects profitable long and short positions continuously by moving broker-held protection to economic break-even and then tightening a native trailing stop through a validated profit ladder.

## ADDED Requirements

### Requirement: Complete stop-policy configuration
The profit-protection monitor SHALL require explicit valid values for initial protection, break-even activation, transaction-cost assumptions, break-even buffer, profit thresholds, trailing gaps, update thresholds, and timing limits before managing a live position.

#### Scenario: Profit ladder is invalid
- **WHEN** profit thresholds are not strictly increasing, trailing gaps are not non-increasing, or any required value is outside its accepted range
- **THEN** live monitoring SHALL refuse to start
- **AND** SHALL identify each invalid policy element

### Requirement: Broker-held protection invariant
An open managed position SHALL retain confirmed broker-held protection throughout monitoring, including while the monitor is disconnected or restarting.

#### Scenario: Monitor becomes unavailable
- **WHEN** the monitor process or its network connection fails
- **THEN** the last confirmed broker-held static or trailing stop SHALL remain active
- **AND** restart recovery SHALL reconcile that stop before changing protection state

### Requirement: Economic break-even
The monitor SHALL calculate economic break-even from weighted average fill price, known or conservatively estimated entry and exit costs, expected slippage, and a configured protective buffer.

#### Scenario: Long reaches break-even activation
- **WHEN** the executable long-side price reaches the configured favorable-profit threshold
- **THEN** the monitor SHALL request a stop no lower than the calculated long break-even level after valid tick-size rounding

#### Scenario: Short reaches break-even activation
- **WHEN** the executable short-side price reaches the configured favorable-profit threshold
- **THEN** the monitor SHALL request a stop no higher than the calculated short break-even level after valid tick-size rounding

#### Scenario: Actual costs become available
- **WHEN** confirmed fills or charges replace estimated transaction costs
- **THEN** the monitor SHALL recalculate break-even without weakening the currently confirmed stop

### Requirement: Favorable price watermark
The monitor SHALL track and durably persist the highest favorable executable price for a long position and the lowest favorable executable price for a short position.

#### Scenario: Unfavorable price movement occurs
- **WHEN** price moves against the position without closing it
- **THEN** the persisted favorable watermark SHALL remain unchanged
- **AND** no protection level SHALL be loosened

### Requirement: Break-even precedes trailing
The monitor SHALL confirm broker-reported break-even protection before activating broker-native trailing for a position.

#### Scenario: Break-even stop is not confirmed
- **WHEN** reconciliation does not confirm an active stop at or beyond economic break-even
- **THEN** the monitor SHALL remain outside trailing state
- **AND** SHALL retain or restore the safest confirmed static protection available

### Requirement: Profit-ladder trailing
After break-even confirmation, the monitor SHALL select a trailing gap from the configured ladder based on maximum favorable profit and SHALL tighten the broker-native trailing parameter when a higher ladder level is reached.

#### Scenario: Position reaches a higher profit level
- **WHEN** maximum favorable profit crosses the next configured threshold
- **THEN** the monitor SHALL request the corresponding trailing gap only if it preserves or improves the protected result

#### Scenario: Profit falls after reaching a level
- **WHEN** current profit drops below a previously reached ladder threshold while the position remains open
- **THEN** the monitor SHALL retain the highest reached level and SHALL NOT widen the trailing gap

### Requirement: Monotonic protection
Every stop transition SHALL be monotonic: a long stop SHALL never decrease and a short stop SHALL never increase, except when explicitly replacing state after the prior position is confirmed closed.

#### Scenario: Requested broker update weakens protection
- **WHEN** the broker-reported result of an update would weaken the prior confirmed stop
- **THEN** the monitor SHALL reject the transition, preserve or restore the prior protection, and enter an operational error state

### Requirement: Controlled update frequency
The monitor SHALL avoid duplicate or immaterial stop updates using configured minimum price improvement, tick-size, cooldown, and idempotency rules.

#### Scenario: Quote changes without meaningful stop improvement
- **WHEN** a quote event does not improve the rounded stop by the configured minimum amount
- **THEN** the monitor SHALL not submit a stop update

### Requirement: Fill-aware protection
Protection state SHALL follow confirmed position quantity, side, and weighted average entry after partial fills, reductions, closures, and reversals.

#### Scenario: Position is partially reduced
- **WHEN** reconciliation confirms a lower open quantity
- **THEN** the monitor SHALL verify broker protection covers the remaining quantity and preserve the applicable break-even and ladder state

#### Scenario: Position closes or reverses
- **WHEN** the original signed position reaches zero or changes side
- **THEN** the monitor SHALL close the original protection state and SHALL initialize any new opposite-side position independently

### Requirement: Recovery and fail-closed behavior
The monitor SHALL persist every confirmed transition and SHALL reconstruct state from local records plus authoritative Tradernet portfolio and order data after restart or event loss.

#### Scenario: Local and broker state disagree
- **WHEN** reconciliation finds an unexplained difference in side, quantity, stop identity, stop level, status, or trailing parameter
- **THEN** the monitor SHALL stop automated transitions for that position
- **AND** SHALL raise an alert while leaving the safest confirmed broker protection in place

#### Scenario: Price gaps through a stop
- **WHEN** a stop executes at a worse price than its trigger due to a market gap or liquidity
- **THEN** the monitor SHALL record actual fills and slippage
- **AND** SHALL reconcile the resulting position without representing the trigger price as guaranteed execution
