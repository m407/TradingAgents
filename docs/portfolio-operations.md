# Portfolio Operations

Install broker support with `pip install "tradingagents[tradernet]"`. Set
`TRADINGAGENTS_PORTFOLIO_CONFIG_JSON`, `TRADERNET_PUBLIC_KEY`, and
`TRADERNET_PRIVATE_KEY` in the service environment. Commands are non-interactive:

```console
tradingagents portfolio reconcile
tradingagents portfolio status
tradingagents portfolio cycle --date 2026-08-19
tradingagents portfolio supervise
```

The cycle directly uses `DEFAULT_CONFIG`, all standard analysts, the configured rating
and risk policy, and authoritative Tradernet account data. Each symbol also requires
broker-provided `lot_size`, `tick_size`, `average_daily_notional`, `instrument_type`, and
`tradable` metadata. Missing metadata fails closed; the command never substitutes live
trading defaults. Live readiness also remains blocked until trusted stop-compatibility
evidence is recorded.

The protection supervisor is assembled directly from the same configuration, broker, and
SQLite state. It reconstructs position-incarnation identity from the broker position ID and
persists favorable watermarks and transition IDs. Missing or contradictory state fails
closed and leaves the safest confirmed broker-held stop in place.

## Live Gate

Output starts with `MODE: READ-ONLY`, `MODE: DRY-RUN`, or `MODE: LIVE`. Live operation
requires both `execution_mode: "live"` in the persistent portfolio configuration and
one runtime gate: `--confirm-live` for an operator invocation, or
`TRADINGAGENTS_PORTFOLIO_SERVICE_LIVE=1` for a service. Remove the runtime variable and
set persistent mode to `dry-run` to disable live operation. Neither gate is inferred.

## Scheduling And Restart

Use external scheduling; there is no embedded scheduler. Run the cycle after the
configured market close from cron or a systemd timer. Run the supervisor as a systemd
service with `Restart=on-failure`. On restart it acquires its account lease and
reconciles positions and broker-held stops before processing transitions. A second
owner fails rather than running concurrently.

Example cron entry (choose the actual post-close time and timezone):

```cron
<minute> <hour> * * 1-5 /opt/tradingagents/bin/tradingagents portfolio cycle --date "$(date +\%F)"
```

Minimal systemd supervisor settings:

```ini
[Service]
Type=simple
EnvironmentFile=/etc/tradingagents/portfolio.env
ExecStart=/opt/tradingagents/bin/tradingagents portfolio supervise
Restart=on-failure
RestartSec=10
```

Back up SQLite with its online backup command while services may be running:

```console
sqlite3 /var/lib/tradingagents/portfolio.db ".backup '/backup/portfolio-$(date +%F).db'"
```

Stop the timer and supervisor before restoring a backup. Keep the database and logs for
audit. For rollback, disable both live gates and stop the application processes. Do not
cancel protective orders as part of application rollback: broker-held stops are left
intact and must be reviewed and cancelled separately by an operator if appropriate.

## Trusted Broker Checks

Read-only smoke checks are scripts, not normal CI:

```console
python scripts/tradernet_smoke.py --symbol AAPL
```

State-changing compatibility probes require an explicit symbol, signed non-zero
quantity, operation-specific price/gap arguments, the flag, and a separate environment
gate. Use only a trusted account and minimal broker-valid quantity:

```console
TRADERNET_COMPATIBILITY_ENABLED=1 python scripts/tradernet_compatibility.py \
  static-stop --symbol AAPL --quantity 1 --stop-price 100 --confirm-state-changes
```

Operations cover static stop, break-even replacement, native long/short trailing
(quantity sign selects side), repeated gap tightening, partial-fill limit submission,
cancellation, and restart reconciliation. Inspect and clean up every broker order after
each probe; the tooling deliberately does not guess safe prices or quantities. Successful
stop probes are recorded in the operational SQLite database. Live cycle and supervisor
readiness require recorded `tighten-trailing` evidence for both long and short positions.
