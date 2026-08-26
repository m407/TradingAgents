# Portfolio Configuration

Install Tradernet support only on hosts that need brokerage access:

```bash
pip install ".[tradernet]"
```

Research-only imports and workflows do not import or require `tradernet-sdk`.

## Credentials

Set `TRADERNET_PUBLIC_KEY` and `TRADERNET_PRIVATE_KEY` in the service environment or an
untracked `.env` file. Both keys are environment-only runtime values. Do not place them in
`TRADINGAGENTS_PORTFOLIO_CONFIG_JSON`, command arguments, source control, logs, reports, or
the operational database.

## Non-Secret Policy

`TRADINGAGENTS_PORTFOLIO_CONFIG_JSON` must be one JSON object matching `PortfolioConfig`.
It contains these required categories:

- `account_scope`: account identifier and IANA market timezone
- `watchlist`: unique symbols
- `rating_weights`: explicit signed weights for Buy, Overweight, Hold, Underweight, and Sell
- `hard_risk_limits`: position, gross/net exposure, notional, liquidity, short, margin, and instrument limits
- `initial_stop`: initial loss fraction and broker order duration
- `break_even`: activation, entry/exit costs, slippage, and protective buffer
- `profit_ladder`: increasing profit fractions with non-increasing trailing gaps
- `timeouts`: request, retry, order, stop-confirmation, and reconnect bounds
- `stop_updates`: cooldown and minimum stop improvement
- `reconciliation`: REST interval, quote polling interval, and maximum quote age
- `database_path`: dedicated operational SQLite path
- `execution_mode`: `dry-run` or `live`; omitted means `dry-run`
- `require_runtime_live_confirmation`: must remain `true` in live mode

No numeric risk, stop, cost, ladder, cooldown, or timeout values are supplied by the project.
Define every value from an operator-approved policy. The model rejects unknown fields, blank
identifiers, duplicate symbols, invalid ranges, an unordered ladder, and a disabled live
confirmation gate.

For deployment tooling that handles smaller structured values separately, these optional JSON
variables replace fields from the complete object:

- `TRADINGAGENTS_PORTFOLIO_WATCHLIST_JSON`
- `TRADINGAGENTS_PORTFOLIO_RATING_WEIGHTS_JSON`
- `TRADINGAGENTS_PORTFOLIO_PROFIT_LADDER_JSON`

Malformed JSON reports the variable name and source location. Credential-like fields in any
non-secret JSON are rejected. `PortfolioConfig.fingerprint()` hashes deterministic validated
non-secret policy data; credentials cannot enter that fingerprint because they use the separate
`TradernetCredentials` model.

## Live Safety

Dry-run is the persistent default. Setting `execution_mode` to `live` is only the first gate.
Live commands must also receive a separate per-invocation or service-environment confirmation
and pass readiness checks. Disabling live operation must not cancel existing broker-held stops.
