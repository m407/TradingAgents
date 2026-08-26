#!/usr/bin/env python3
"""Explicitly gated Tradernet state-changing compatibility probes (never CI)."""

import argparse
import json
import os
import uuid
from decimal import Decimal

from tradingagents.portfolio.config import TradernetCredentials, load_portfolio_config
from tradingagents.portfolio.store import PortfolioStore
from tradingagents.portfolio.tradernet import TradernetAdapter

GATE_ENV = "TRADERNET_COMPATIBILITY_ENABLED"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("static-stop", "break-even", "trailing", "tighten-trailing", "partial-fill", "cancel", "restart"),
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--quantity", required=True, type=Decimal)
    parser.add_argument("--stop-price", type=Decimal)
    parser.add_argument("--trailing-gap", type=Decimal)
    parser.add_argument("--tightened-gap", type=Decimal)
    parser.add_argument("--limit-price", type=Decimal)
    parser.add_argument("--order-id")
    parser.add_argument("--client-order-id")
    parser.add_argument("--confirm-state-changes", action="store_true")
    args = parser.parse_args()
    if not args.confirm_state_changes or os.environ.get(GATE_ENV) != "1":
        parser.error(f"requires --confirm-state-changes and {GATE_ENV}=1")
    if args.quantity == 0:
        parser.error("--quantity must be non-zero and use the intended long/short sign")

    config = load_portfolio_config()
    broker = TradernetAdapter(
        TradernetCredentials.from_environment(),
        timeout=float(config.timeouts.request_seconds),
        read_attempts=config.timeouts.read_attempts,
        sensitive_identifiers=(config.account_scope.account_id,),
    )
    identity = f"compat-{args.operation}-{uuid.uuid4()}"
    if args.operation in {"static-stop", "break-even"}:
        if args.stop_price is None:
            parser.error("this operation requires --stop-price")
        result = broker.set_static_stop(
            symbol=args.symbol,
            signed_position_quantity=args.quantity,
            stop_price=args.stop_price,
            transition_id=identity,
        )
    elif args.operation in {"trailing", "tighten-trailing"}:
        if args.stop_price is None or args.trailing_gap is None:
            parser.error("this operation requires --stop-price and --trailing-gap")
        result = broker.set_trailing_stop(
            symbol=args.symbol,
            signed_position_quantity=args.quantity,
            stop_price=args.stop_price,
            trailing_percent=args.trailing_gap,
            transition_id=identity,
        )
        if args.operation == "tighten-trailing":
            if args.tightened_gap is None or args.tightened_gap >= args.trailing_gap:
                parser.error("--tightened-gap must be present and smaller than --trailing-gap")
            result = broker.set_trailing_stop(
                symbol=args.symbol,
                signed_position_quantity=args.quantity,
                stop_price=args.stop_price,
                trailing_percent=args.tightened_gap,
                transition_id=f"{identity}-tightened",
            )
    elif args.operation == "partial-fill":
        if args.limit_price is None:
            parser.error("partial-fill probe requires --limit-price")
        result = broker.place_order(
            symbol=args.symbol,
            signed_quantity=args.quantity,
            order_type="limit",
            duration="day",
            margin=False,
            client_order_id=identity,
            limit_price=args.limit_price,
        )
    elif args.operation == "cancel":
        if not args.order_id or not args.client_order_id:
            parser.error("cancel requires --order-id and its original --client-order-id")
        result = broker.cancel_order(args.order_id, client_order_id=args.client_order_id)
    else:
        result = broker.get_active_orders()
    if args.operation in {"static-stop", "break-even", "trailing", "tighten-trailing"}:
        PortfolioStore(
            config.database_path, account_scope=config.account_scope.account_id
        ).record_compatibility_evidence(
            args.operation,
            "long" if args.quantity > 0 else "short",
            {"symbol": args.symbol, "quantity": str(args.quantity), "result": broker.sanitize(str(result))},
        )
    print(json.dumps({"mode": "LIVE COMPATIBILITY", "operation": args.operation, "result": broker.sanitize(str(result))}))


if __name__ == "__main__":
    main()
