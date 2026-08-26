from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import ModuleType, SimpleNamespace

from typer.testing import CliRunner

import cli.portfolio as portfolio
from tradingagents.portfolio.config import ExecutionMode

runner = CliRunner()


def test_live_gate_requires_persistent_and_runtime_gate(monkeypatch):
    config = SimpleNamespace(execution_mode=ExecutionMode.LIVE)
    monkeypatch.setattr(portfolio, "_runtime", lambda: (config, object(), object()))
    monkeypatch.delenv(portfolio.SERVICE_LIVE_ENV, raising=False)

    result = runner.invoke(portfolio.app, ["cycle", "--date", "2026-08-19"])

    assert result.exit_code == 1
    assert "MODE: DRY-RUN" in result.output
    assert "requires two gates" in result.output


def test_dry_run_cycle_calls_direct_runner(monkeypatch):
    calls = []
    config = SimpleNamespace(execution_mode=ExecutionMode.DRY_RUN)
    broker = SimpleNamespace(sanitize=lambda value: value)
    assembled = SimpleNamespace(
        run=lambda trading_date, confirm_live: calls.append((trading_date, confirm_live))
        or {"dry_run": True}
    )
    monkeypatch.setattr(portfolio, "_runtime", lambda: (config, object(), broker))
    monkeypatch.setattr(portfolio, "_build_cycle", lambda *args: assembled)

    result = runner.invoke(portfolio.app, ["cycle", "--date", "2026-08-19"])

    assert result.exit_code == 0
    assert "MODE: DRY-RUN" in result.output
    assert calls == [(portfolio.date(2026, 8, 19), False)]


def test_commands_have_no_factory_option():
    cycle_help = runner.invoke(portfolio.app, ["cycle", "--help"])
    supervise_help = runner.invoke(portfolio.app, ["supervise", "--help"])

    assert cycle_help.exit_code == supervise_help.exit_code == 0
    assert "--factory" not in cycle_help.output + supervise_help.output


def test_direct_cycle_composes_standard_dependencies(monkeypatch):
    captured = {}
    graph = SimpleNamespace(save_reports=lambda *_args: None)

    def build_graph(**kwargs):
        captured["graph"] = kwargs
        return graph

    graph_module = ModuleType("tradingagents.graph.trading_graph")
    graph_module.TradingAgentsGraph = build_graph
    monkeypatch.setitem(sys.modules, "tradingagents.graph.trading_graph", graph_module)
    monkeypatch.setattr(
        portfolio.PortfolioCycle,
        "from_config",
        lambda config, **kwargs: captured.setdefault("cycle", (config, kwargs)),
    )
    config = SimpleNamespace()

    portfolio._build_cycle(config, object(), object())

    assert captured["graph"]["selected_analysts"] == portfolio._ANALYSTS
    dependencies = captured["cycle"][1]
    assert isinstance(dependencies["policy"], portfolio._DirectPolicy)
    assert isinstance(dependencies["reconciler"], portfolio.AuthoritativeReconciler)
    assert dependencies["report_writer"] == graph.save_reports
    assert callable(dependencies["initial_stop"])
    assert callable(dependencies["readiness_check"])


def test_direct_policy_plans_with_authoritative_broker_metadata():
    hard_risk = SimpleNamespace(
        max_abs_position_weight=Decimal("0.2"),
        max_gross_exposure=Decimal("1"),
        max_abs_net_exposure=Decimal("1"),
        max_order_notional=Decimal("1000"),
        max_position_notional=Decimal("1000"),
        min_average_daily_notional=Decimal("10000"),
        allow_short=False,
        allow_margin=False,
        permitted_instrument_types=("stock",),
    )
    config = SimpleNamespace(
        execution_mode=ExecutionMode.DRY_RUN,
        rating_weights=SimpleNamespace(
            buy=Decimal("0.1"),
            overweight=Decimal("0.05"),
            hold=Decimal(0),
            underweight=Decimal("-0.05"),
            sell=Decimal("-0.1"),
        ),
        hard_risk_limits=hard_risk,
        reconciliation=SimpleNamespace(maximum_quote_age_seconds=30),
        initial_stop=SimpleNamespace(order_duration="day"),
    )
    broker = SimpleNamespace(
        get_active_orders=lambda: (),
        get_market_status=lambda _symbol: SimpleNamespace(is_open=True),
        get_instrument_metadata=lambda _symbol: {
            "lot_size": "1",
            "tick_size": "0.01",
            "average_daily_notional": "100000",
            "instrument_type": "stock",
            "tradable": True,
        },
    )
    snapshot = SimpleNamespace(
        balances=(SimpleNamespace(equity="1000", available="1000"),),
        positions=(),
    )
    quote = SimpleNamespace(
        bid=Decimal("9.99"),
        ask=Decimal("10"),
        as_of=datetime.now(timezone.utc),
    )

    plan = portfolio._DirectPolicy(config, broker).plan(
        snapshot,
        {"ABC": SimpleNamespace(rating="Buy")},
        {"ABC": quote},
        "cycle-1",
    )

    assert plan.orders[0].signed_quantity == Decimal("10")
    assert plan.orders[0].risk_decisions[0].approved


def test_status_is_sanitized_and_has_all_sections(tmp_path, monkeypatch):
    account = "sensitive-account"
    config = SimpleNamespace(
        database_path=tmp_path / "portfolio.db",
        account_scope=SimpleNamespace(account_id=account),
    )
    monkeypatch.setattr(portfolio, "load_portfolio_config", lambda: config)
    store = portfolio.PortfolioStore(config.database_path, account_scope=account)
    store.raise_alert("ERROR", f"problem for {account}", {})

    result = runner.invoke(portfolio.app, ["status"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == {"cycles", "protection", "pending_unknown", "alerts"}
    assert account not in result.output
    assert payload["alerts"][0]["message"] == "problem for <redacted>"
