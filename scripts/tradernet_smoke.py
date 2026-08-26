#!/usr/bin/env python3
"""Trusted-environment, read-only Tradernet smoke checks (not pytest/CI)."""

import argparse
import json

from tradingagents.portfolio.config import TradernetCredentials, load_portfolio_config
from tradingagents.portfolio.tradernet import TradernetAdapter


def shape(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "__dataclass_fields__"):
        value = {name: getattr(value, name) for name in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return {"type": "array", "count": len(value), "items": shape(value[0]) if value else None}
    return type(value).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", required=True)
    args = parser.parse_args()
    config = load_portfolio_config()
    broker = TradernetAdapter(
        TradernetCredentials.from_environment(),
        timeout=float(config.timeouts.request_seconds),
        read_attempts=config.timeouts.read_attempts,
        sensitive_identifiers=(config.account_scope.account_id,),
    )
    snapshot = broker.get_portfolio()
    results = {
        "mode": "READ-ONLY",
        "account_summary": shape(snapshot),
        "active_orders": shape(broker.get_active_orders()),
        "market_status": shape(broker.get_market_status(args.symbol[0])),
        "quotes": {symbol: shape(broker.get_quote(symbol)) for symbol in args.symbol},
    }
    print(json.dumps(results, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
