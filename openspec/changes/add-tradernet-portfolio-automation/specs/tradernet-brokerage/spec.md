## Purpose

Provides a safe, typed boundary between TradingAgents and Tradernet so portfolio automation can observe broker state and perform explicitly authorized trading operations without depending on raw or ambiguous API responses.

## ADDED Requirements

### Requirement: API-key authentication
The system SHALL authenticate Tradernet requests with a public/private API-key pair, SHALL obtain the private key only from secret-bearing runtime configuration, and SHALL NOT persist or include the private key in logs, reports, checkpoints, or audit payloads.

#### Scenario: Authenticated request
- **WHEN** valid Tradernet API credentials are configured
- **THEN** the system SHALL sign and send authenticated broker requests
- **AND** SHALL redact credentials and replayable authentication material from diagnostics

#### Scenario: Credentials are unavailable
- **WHEN** an operation requiring Tradernet authentication starts without both required keys
- **THEN** the system SHALL reject the operation before sending a broker request
- **AND** SHALL identify the missing configuration without printing credential values

### Requirement: Normalized broker state
The system SHALL expose normalized account balances, signed positions, quotes, orders, fills, and market status while retaining Tradernet identifiers required for reconciliation.

#### Scenario: Portfolio contains long and short positions
- **WHEN** Tradernet returns open positions with positive and negative quantities
- **THEN** the normalized snapshot SHALL preserve signed quantity, average entry price, current value, currency, profit information, and broker position identity for each position

#### Scenario: Broker response is incomplete
- **WHEN** a required field is missing or cannot be converted to its declared domain type
- **THEN** the system SHALL reject the affected snapshot as invalid
- **AND** SHALL NOT infer a tradable position or successful order from incomplete data

### Requirement: Explicit time ranges
The system SHALL use an explicitly evaluated current time and explicit start/end ranges for time-dependent Tradernet requests.

#### Scenario: Long-running process requests current candles
- **WHEN** a process requests candles after remaining active across one or more date boundaries
- **THEN** the request end time SHALL reflect the time of that invocation rather than process import or startup time

### Requirement: Classified errors and bounded requests
The system SHALL recognize all documented Tradernet error shapes, apply bounded timeouts, and distinguish retryable read failures from non-retryable validation, authorization, and trading failures.

#### Scenario: Broker returns an application error
- **WHEN** a response contains `error`, `errorMsg`, or `errMsg`
- **THEN** the system SHALL report a failed operation with the broker code and sanitized message
- **AND** SHALL NOT expose the response as a successful domain result

#### Scenario: Read request fails transiently
- **WHEN** an idempotent portfolio, quote, market-status, or order-status request fails with a retryable transport error
- **THEN** the system SHALL retry within configured attempt and time limits

#### Scenario: Trading request outcome is unknown
- **WHEN** a state-changing request times out or loses its response after transmission
- **THEN** the system SHALL reconcile broker orders using its idempotency identity before considering another submission
- **AND** SHALL NOT blindly retry the state-changing request

### Requirement: Explicit order intent
The system SHALL submit orders using an explicit signed quantity, order type, duration, margin policy, and caller-provided idempotency identity, and SHALL prevent an order from implicitly changing from position reduction to position expansion.

#### Scenario: Reduce a long position
- **WHEN** an intent requests selling fewer units than the current long quantity
- **THEN** the submitted order SHALL be classified as a long reduction
- **AND** SHALL NOT open a short position if broker state changed before submission

#### Scenario: Open a short position
- **WHEN** an approved intent explicitly requests a negative target exposure and short trading is enabled
- **THEN** the system SHALL submit a short-opening order only after pre-trade reconciliation and risk validation

### Requirement: Order and stop operations
The system SHALL support placing market and limit orders, querying active and historical orders, cancelling a specific order, setting a static protective stop, and setting or updating a broker-native trailing stop.

#### Scenario: Stop update is accepted
- **WHEN** Tradernet accepts a requested protective-stop change
- **THEN** the system SHALL query active orders and confirm the broker-reported symbol, side, quantity, status, stop price, and trailing percentage before marking the change successful

#### Scenario: Stop update cannot be confirmed
- **WHEN** the requested stop is missing, rejected, inactive, or materially different after reconciliation
- **THEN** the operation SHALL remain failed or unknown
- **AND** downstream automation SHALL treat the position as not safely protected

### Requirement: Event subscriptions with reconciliation
The system SHALL consume quote, portfolio, order, and market-status events and SHALL recover from interrupted event streams using reconnection plus authoritative REST reconciliation.

#### Scenario: Event stream reconnects
- **WHEN** a Tradernet event stream disconnects
- **THEN** the system SHALL reconnect with bounded backoff, restore required subscriptions, and reconcile current broker state before processing new trading transitions
