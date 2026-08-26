"""Non-interactive portfolio operations."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio.config import (
    ExecutionMode,
    TradernetCredentials,
    load_portfolio_config,
)
from tradingagents.portfolio.cycle import CyclePlan, PortfolioCycle
from tradingagents.portfolio.policy import (
    RiskContext,
    RiskLimits,
    evaluate_risk,
    map_rating_to_weight,
    plan_target_intents,
    signed_target_quantity,
)
from tradingagents.portfolio.protection import (
    PositionSide,
    ProtectionPolicy,
    initial_stop_price,
)
from tradingagents.portfolio.reconciliation import AuthoritativeReconciler
from tradingagents.portfolio.store import PortfolioStore
from tradingagents.portfolio.tradernet import TradernetAdapter

app = typer.Typer(help="Non-interactive Tradernet portfolio operations.")
SERVICE_LIVE_ENV = "TRADINGAGENTS_PORTFOLIO_SERVICE_LIVE"
_UNKNOWN_STATES = ("unknown", "pending-unknown", "outcome-unknown")
_ANALYSTS = ("market", "social", "news", "fundamentals")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "__dataclass_fields__"):
        return {name: _jsonable(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _emit(value: Any) -> None:
    typer.echo(json.dumps(_jsonable(value), ensure_ascii=True, sort_keys=True))


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


def _runtime() -> tuple[Any, PortfolioStore, TradernetAdapter]:
    try:
        config = load_portfolio_config()
        credentials = TradernetCredentials.from_environment()
        store = PortfolioStore(
            config.database_path,
            account_scope=config.account_scope.account_id,
        )
        broker = TradernetAdapter(
            credentials,
            timeout=float(config.timeouts.request_seconds),
            read_attempts=config.timeouts.read_attempts,
            sensitive_identifiers=(config.account_scope.account_id,),
        )
        return config, store, broker
    except Exception as exc:
        message = str(exc)
        if "optional SDK" in message or "Tradernet support requires" in message:
            message = 'Tradernet support is not installed; run `pip install "tradingagents[tradernet]"`.'
        _fail(message)


def _live_confirmation(config: Any, confirm_live: bool) -> bool:
    persistent = config.execution_mode is ExecutionMode.LIVE
    runtime = confirm_live or os.environ.get(SERVICE_LIVE_ENV) == "1"
    typer.echo(f"MODE: {'LIVE' if persistent and runtime else 'DRY-RUN'}")
    if persistent != runtime and (persistent or runtime):
        missing = (
            f"--confirm-live or {SERVICE_LIVE_ENV}=1"
            if persistent
            else "persistent execution_mode=live"
        )
        _fail(f"live execution requires two gates; missing {missing}")
    return persistent and runtime


def _value(value: Any, name: str, default: Any = None) -> Any:
    return (
        value.get(name, default)
        if isinstance(value, Mapping)
        else getattr(value, name, default)
    )


class _DirectPolicy:
    def __init__(self, config: Any, broker: Any) -> None:
        self.config = config
        self.broker = broker
        self.metadata: dict[str, dict[str, Any]] = {}

    def _metadata(self, symbol: str) -> dict[str, Any]:
        if symbol in self.metadata:
            return self.metadata[symbol]
        reader = getattr(self.broker, "get_instrument_metadata", None)
        if not callable(reader):
            raise ValueError(
                "broker does not provide authoritative instrument metadata; "
                f"{symbol} requires lot_size, tick_size, average_daily_notional, "
                "instrument_type, and tradable"
            )
        raw = reader(symbol)
        required = (
            "lot_size",
            "tick_size",
            "average_daily_notional",
            "instrument_type",
            "tradable",
        )
        missing = tuple(name for name in required if _value(raw, name) is None)
        if missing:
            raise ValueError(f"broker metadata for {symbol} is missing: {', '.join(missing)}")
        metadata = {name: _value(raw, name) for name in required}
        for name in ("lot_size", "tick_size", "average_daily_notional"):
            metadata[name] = Decimal(str(metadata[name]))
            if metadata[name] <= 0:
                raise ValueError(f"broker metadata {name} for {symbol} must be positive")
        if not isinstance(metadata["tradable"], bool):
            raise ValueError(f"broker metadata tradable for {symbol} must be boolean")
        if not str(metadata["instrument_type"]).strip():
            raise ValueError(f"broker metadata instrument_type for {symbol} must not be blank")
        self.metadata[symbol] = metadata
        return metadata

    def plan(
        self,
        snapshot: Any,
        research: Mapping[str, Any],
        quotes: Mapping[str, Any],
        cycle_id: str,
    ) -> CyclePlan:
        balances = tuple(_value(snapshot, "balances", ()))
        if len(balances) != 1:
            raise ValueError("direct portfolio policy requires exactly one broker account balance")
        balance = balances[0]
        equity = Decimal(str(_value(balance, "equity")))
        if equity <= 0:
            raise ValueError("broker account equity must be positive")
        positions = {
            str(_value(position, "symbol")).upper(): position
            for position in _value(snapshot, "positions", ())
        }
        gross = sum(
            (
                abs(Decimal(str(_value(position, "market_value"))))
                for position in positions.values()
            ),
            Decimal(0),
        )
        net = sum(
            (Decimal(str(_value(position, "market_value"))) for position in positions.values()),
            Decimal(0),
        )
        active_orders = tuple(self.broker.get_active_orders())
        limits = RiskLimits.from_config(self.config)
        available_margin = _value(snapshot, "available_margin")
        if self.config.hard_risk_limits.allow_margin and available_margin is None:
            raise ValueError(
                "broker account snapshot is missing available_margin required by margin policy"
            )
        available_margin = Decimal(str(available_margin or 0))
        target_plans = []
        decisions = []
        decisions_by_symbol = {}
        now = datetime.now(timezone.utc)

        for symbol, symbol_research in research.items():
            metadata = self._metadata(symbol)
            quote = quotes[symbol]
            position = positions.get(symbol)
            current_quantity = Decimal(str(_value(position, "quantity", 0)))
            current_value = Decimal(str(_value(position, "market_value", 0)))
            current_weight = current_value / equity
            weight = map_rating_to_weight(
                _value(symbol_research, "rating"),
                self.config.rating_weights,
                current_weight=current_weight,
                held=position is not None,
            )
            if weight.target_weight == current_weight:
                target_quantity = current_quantity
                price = Decimal(
                    str(_value(quote, "ask" if current_quantity >= 0 else "bid"))
                )
            else:
                price = Decimal(
                    str(_value(quote, "ask" if weight.target_weight >= 0 else "bid"))
                )
                target_quantity = signed_target_quantity(
                    weight.target_weight, equity, price, metadata["lot_size"]
                )
            quote_time = _value(quote, "as_of")
            if not isinstance(quote_time, datetime) or quote_time.tzinfo is None:
                raise ValueError(f"broker quote timestamp for {symbol} is unavailable or naive")
            market = self.broker.get_market_status(symbol)
            decision = evaluate_risk(
                symbol=symbol,
                current_quantity=current_quantity,
                target_quantity=target_quantity,
                price=price,
                lot_size=metadata["lot_size"],
                limits=limits,
                context=RiskContext(
                    equity=equity,
                    gross_exposure=gross,
                    net_exposure=net,
                    available_cash=Decimal(str(_value(balance, "available"))),
                    available_margin=available_margin,
                    average_daily_notional=metadata["average_daily_notional"],
                    quote_age=now - quote_time.astimezone(timezone.utc),
                    market_open=bool(_value(market, "is_open")),
                    instrument_permitted=metadata["tradable"],
                    instrument_type=str(metadata["instrument_type"]),
                    conflicting_order=any(
                        str(_value(order, "symbol")).upper() == symbol for order in active_orders
                    ),
                ),
                live=self.config.execution_mode is ExecutionMode.LIVE,
            )
            decisions.append(decision)
            decisions_by_symbol[symbol] = decision
            target_plans.append(
                plan_target_intents(
                    symbol,
                    current_quantity,
                    decision.target_quantity,
                    lot_size=metadata["lot_size"],
                    tick_size=metadata["tick_size"],
                )
            )

        plan = CyclePlan.from_policy_result(tuple(target_plans), cycle_id)
        orders = tuple(
            replace(
                order,
                duration=self.config.initial_stop.order_duration,
                margin=self.config.hard_risk_limits.allow_margin,
                risk_decisions=(decisions_by_symbol[order.symbol],),
            )
            for order in plan.orders
        )
        return CyclePlan(
            orders,
            tuple(decisions),
            tuple(
                {"symbol": order.symbol, "action": "attach-initial-stop-after-fill"}
                for order in orders
                if order.exposure_increasing
            ),
            tuple(order.expected_transition for order in orders),
        )


def _build_cycle(config: Any, store: Any, broker: Any) -> PortfolioCycle:
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph(selected_analysts=_ANALYSTS, config=DEFAULT_CONFIG.copy())
    policy = _DirectPolicy(config, broker)

    def calculate_initial_stop(order: Any, _filled: Decimal, _confirmation: Any) -> Decimal:
        snapshot = broker.get_portfolio()
        position = next(
            (
                item
                for item in snapshot.positions
                if item.symbol.upper() == order.symbol
                and (item.quantity > 0) == (order.signed_quantity > 0)
            ),
            None,
        )
        if position is None:
            raise ValueError(f"filled position for {order.symbol} is absent during stop calculation")
        protection = ProtectionPolicy.from_config(
            config, tick_size=policy._metadata(order.symbol)["tick_size"]
        )
        return initial_stop_price(
            protection,
            side=PositionSide.from_quantity(position.quantity),
            average_entry=position.average_price,
        )

    def readiness(_broker: Any, _store: Any, _snapshot: Any) -> Mapping[str, bool | str]:
        return {
            "credentials": True,
            "optional_dependency": True,
            "configuration": True,
            "database_ownership": True,
            "broker_connectivity": True,
            "reconciliation": True,
            "market_data": bool(policy.metadata),
            "hard_risk": not RiskLimits.from_config(config).missing_live_fields(),
            "stop_compatibility": store.has_stop_compatibility_evidence()
            or "trusted long/short trailing compatibility evidence is not recorded",
        }

    return PortfolioCycle.from_config(
        config,
        graph=graph,
        broker=broker,
        store=store,
        policy=policy,
        reconciler=AuthoritativeReconciler(),
        report_writer=graph.save_reports,
        initial_stop=calculate_initial_stop,
        readiness_check=readiness,
    )


def _build_supervisor(_config: Any, _store: Any, _broker: Any) -> Any:
    _fail(
        "protection supervisor cannot be assembled safely: persisted state does not contain "
        "the authoritative position-incarnation identity, confirmed transition history, and "
        "watermark required to reconstruct monotonic protection after restart"
    )


@app.command("reconcile")
def reconcile() -> None:
    """Read and reconcile authoritative Tradernet state without broker mutations."""
    typer.echo("MODE: READ-ONLY")
    config, store, broker = _runtime()
    try:
        snapshot = broker.get_portfolio()
        active = broker.get_active_orders()
        history = broker.get_order_history()
        fills = broker.get_fills()
        result = AuthoritativeReconciler().reconcile(
            store.list_intents(config.account_scope.account_id),
            snapshot.positions,
            active,
            history,
            fills,
        )
        _emit(
            {
                "state": result.state,
                "positions": len(snapshot.positions),
                "active_orders": len(active),
                "historical_orders": len(history),
                "fills": len(fills),
                "intent_resolutions": len(result.resolutions),
                "discrepancies": [item.code for item in result.discrepancies],
            }
        )
    except Exception as exc:
        _fail(str(broker.sanitize(str(exc))))


@app.command("cycle")
def cycle(
    trading_date: Annotated[str, typer.Option("--date", help="Trading date (YYYY-MM-DD).")],
    confirm_live: Annotated[bool, typer.Option("--confirm-live")] = False,
) -> None:
    """Run one idempotent portfolio cycle."""
    try:
        parsed_date = date.fromisoformat(trading_date)
    except ValueError:
        _fail("--date must use YYYY-MM-DD")
    config, store, broker = _runtime()
    live = _live_confirmation(config, confirm_live)
    runner = _build_cycle(config, store, broker)
    try:
        result = runner.run(parsed_date, confirm_live=live)
        _emit(result)
    except Exception as exc:
        _fail(str(broker.sanitize(str(exc))))


@app.command("supervise")
def supervise(
    confirm_live: Annotated[bool, typer.Option("--confirm-live")] = False,
) -> None:
    """Run the continuous broker-held protection supervisor."""
    config, store, broker = _runtime()
    if not _live_confirmation(config, confirm_live):
        _fail("the protection supervisor requires both live gates; broker stops are unchanged")
    if not store.has_stop_compatibility_evidence():
        _fail(
            "trusted long/short trailing compatibility evidence is not recorded; "
            "broker stops are unchanged"
        )
    supervisor = _build_supervisor(config, store, broker)
    try:
        supervisor.start()
        typer.echo("protection supervisor running; broker-held stops remain active across restarts")
        while True:
            supervisor.tick()
            time.sleep(float(config.reconciliation.quote_poll_seconds))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _fail(str(broker.sanitize(str(exc))))
    finally:
        supervisor.stop()


@app.command("status")
def status(
    database: Annotated[Path | None, typer.Option("--database")] = None,
    limit: Annotated[int, typer.Option(min=1, max=500)] = 20,
) -> None:
    """Print sanitized cycle, protection, unknown-outcome, and alert status."""
    try:
        config = load_portfolio_config()
        path = database or config.database_path
        account = config.account_scope.account_id
        store = PortfolioStore(path, account_scope=account)
        with store.connect() as connection:
            cycles = connection.execute(
                "SELECT cycle_identity, trading_date, status, created_at, updated_at "
                "FROM cycle_runs WHERE account_scope = ? ORDER BY id DESC LIMIT ?",
                (account, limit),
            ).fetchall()
            protection = connection.execute(
                "SELECT incarnation_identity, symbol, side, state, created_at, updated_at "
                "FROM protection_states WHERE account_scope = ? ORDER BY id DESC LIMIT ?",
                (account, limit),
            ).fetchall()
            unknown_intents = connection.execute(
                "SELECT intent_identity AS identity, symbol, status, created_at, updated_at "
                "FROM intents WHERE account_scope = ? AND status IN (?, ?, ?) "
                "ORDER BY id DESC LIMIT ?",
                (account, *_UNKNOWN_STATES, limit),
            ).fetchall()
            unknown_operations = connection.execute(
                "SELECT o.operation_identity AS identity, COALESCE(i.symbol, p.symbol) AS symbol, "
                "o.state AS status, o.created_at, o.updated_at FROM broker_operations o "
                "LEFT JOIN intents i ON i.id = o.intent_id "
                "LEFT JOIN protection_transitions t ON t.id = o.transition_id "
                "LEFT JOIN protection_states p ON p.id = t.protection_state_id "
                "WHERE COALESCE(i.account_scope, p.account_scope) = ? "
                "AND o.state IN (?, ?, ?) ORDER BY o.id DESC LIMIT ?",
                (account, *_UNKNOWN_STATES, limit),
            ).fetchall()
            alerts = connection.execute(
                "SELECT id, severity, message, created_at FROM alerts "
                "WHERE account_scope = ? ORDER BY id DESC LIMIT ?",
                (account, limit),
            ).fetchall()
        safe_alerts = [dict(row) for row in alerts]
        sensitive = (
            account,
            os.environ.get("TRADERNET_PUBLIC_KEY", ""),
            os.environ.get("TRADERNET_PRIVATE_KEY", ""),
        )
        for alert in safe_alerts:
            for value in sensitive:
                if value:
                    alert["message"] = alert["message"].replace(value, "<redacted>")
        _emit(
            {
                "cycles": [dict(row) for row in cycles],
                "protection": [dict(row) for row in protection],
                "pending_unknown": [
                    dict(row) for row in (*unknown_intents, *unknown_operations)
                ][:limit],
                "alerts": safe_alerts,
            }
        )
    except Exception as exc:
        message = str(exc)
        if account := locals().get("account"):
            message = message.replace(account, "<redacted>")
        _fail(message)


if __name__ == "__main__":
    app()
