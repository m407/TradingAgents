from datetime import date, timedelta
from decimal import Decimal as D

import numpy as np
import pytest
from pydantic import ValidationError
from trader_consultant import (
    OptimizationRequest,
    RebalanceRequest,
    compute_optimization,
    compute_rebalance,
)
from trader_consultant.rebalance import equal_budgets


def test_rebalance_includes_cash_and_requires_explicit_holdings():
    req = RebalanceRequest(
        holdings={"A": "600", "B": "400"},
        cash="1000",
        targets={"A": ".25", "B": ".25"},
        prices={"A": "10", "B": "20"},
    )
    result = compute_rebalance(req)
    assert result["total_value"] == D(2000)
    assert result["cash_target_value"] == D(1000)
    assert [r["trade_shares"] for r in result["positions"]] == [D(-10), D(5)]
    with pytest.raises(ValidationError, match="explicit target"):
        RebalanceRequest(holdings={"A": "100"}, cash="0", targets={"B": "1"})


@pytest.mark.parametrize(
    "changes",
    [
        {"cash": "-1"},
        {"cash": "Infinity"},
        {"holdings": {"A": "-1"}},
        {"targets": {"A": ".8", "B": ".8"}},
        {"prices": {"A": "0"}},
        {"prices": {"C": "1"}},
        {"targets": {"A": "NaN"}},
    ],
)
def test_rebalance_rejects_invalid_arithmetic(changes):
    data = {"holdings": {"A": "100"}, "cash": "10", "targets": {"A": ".5"}}
    data.update(changes)
    with pytest.raises(ValidationError):
        RebalanceRequest(**data)


def test_equal_allocation_caps_and_retains_excess():
    assert equal_budgets(D(90), {"A": D(10), "B": D(100), "C": D(100)}) == {
        "A": D(10),
        "B": D(40),
        "C": D(40),
    }
    assert sum(equal_budgets(D(500), {"A": D(10), "B": D(20)}).values()) == 30
    assert sum(equal_budgets(D(1), dict.fromkeys(["A", "B", "C"], D(1))).values()) <= 1


def histories():
    rng = np.random.default_rng(42)
    returns = rng.normal([0.001, 0.002, 0.003], [0.01, 0.02, 0.03], size=(300, 3))
    prices = 100 * np.cumprod(1 + returns, axis=0)
    return {
        "prices": {name: prices[:, i].tolist() for i, name in enumerate(["A", "B", "C"])},
        "dates": [date(2025, 1, 1) + timedelta(days=i) for i in range(len(prices))],
    }


@pytest.mark.parametrize("method", ["min_variance", "max_sharpe", "mean_variance", "risk_parity"])
def test_all_transferred_optimizers_return_valid_weights(method):
    data = histories()
    if method == "mean_variance":
        values = np.array(list(data["prices"].values()))
        target = float(np.mean((values[:, 1:] / values[:, :-1] - 1).mean(axis=1) * 252))
    else:
        target = None
    result = compute_optimization(
        OptimizationRequest(
            **data,
            method=method,
            risk_free_annual=0.02,
            target_return=target,
        )
    )
    assert sum(result["weights"].values()) == pytest.approx(1)
    assert all(-1e-7 <= w <= 1 + 1e-7 for w in result["weights"].values())
    assert result["annual_volatility"] > 0
    if method == "risk_parity":
        assert list(result["risk_contributions"].values()) == pytest.approx([1 / 3] * 3, abs=1e-5)
    if method == "mean_variance":
        assert result["annual_return"] == pytest.approx(target)


def test_invalid_or_unaligned_price_inputs():
    for value in [float("nan"), float("inf"), 0, -1]:
        data = histories()
        data["prices"]["A"][0] = value
        with pytest.raises(ValidationError):
            OptimizationRequest(**data, method="min_variance")
    data = histories()
    data["prices"]["A"].pop()
    with pytest.raises(ValidationError, match="same dates"):
        OptimizationRequest(**data, method="min_variance")


def test_optimizer_rejects_degenerate_covariance_and_unreachable_target():
    data = histories()
    data["prices"]["B"] = data["prices"]["A"]
    with pytest.raises(ValueError, match="degenerate"):
        compute_optimization(OptimizationRequest(**data, method="min_variance"))
    with pytest.raises(ValueError, match="unreachable"):
        compute_optimization(
            OptimizationRequest(**histories(), method="mean_variance", target_return=100)
        )


def test_max_sharpe_requires_risk_free_rate():
    with pytest.raises(ValidationError):
        OptimizationRequest(**histories(), method="max_sharpe")


def test_documented_math_examples_execute():
    import re
    from pathlib import Path

    document = (Path(__file__).resolve().parents[1] / "docs" / "tools.md").read_text()
    for snippet in re.findall(r"```python\n(.*?)```", document, re.S):
        exec(compile(snippet, "docs/tools.md", "exec"), {})


def test_calendars_require_unique_increasing_dates():
    data = histories()
    data["dates"][1] = data["dates"][0]
    with pytest.raises(ValidationError, match="unique"):
        OptimizationRequest(**data, method="min_variance")
