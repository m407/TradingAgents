# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0
"""Adapted Trading-R1 rebalance arithmetic: Decimal, explicit cash, explicit targets."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field, model_validator

from .models import Amount, Model, Positive, Ticker, Weight

ZERO = Decimal(0)


class RebalanceRequest(Model):
    holdings: Annotated[dict[Ticker, Amount], Field(max_length=500)]
    targets: Annotated[dict[Ticker, Weight], Field(max_length=500)]
    cash: Amount
    prices: dict[Ticker, Positive] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check(self):
        if sum(self.holdings.values(), self.cash) <= 0:
            raise ValueError("portfolio has no value")
        if sum(self.targets.values(), ZERO) > 1:
            raise ValueError("target weights sum above 1")
        if set(self.holdings) - set(self.targets):
            raise ValueError("every holding needs an explicit target; omission never means sell")
        if set(self.prices) - (set(self.holdings) | set(self.targets)):
            raise ValueError("unknown price ticker")
        return self


def compute_rebalance(req: RebalanceRequest) -> dict:
    """Pure ideal quantities, without lots, costs or broker execution."""
    total = sum(req.holdings.values(), req.cash)
    rows = []
    for name in sorted(set(req.holdings) | set(req.targets)):
        current = req.holdings.get(name, ZERO)
        target_w = req.targets[name]
        trade = target_w * total - current
        row = {
            "name": name,
            "current_value": current,
            "current_weight": current / total,
            "target_weight": target_w,
            "drift": current / total - target_w,
            "trade_value": trade,
            "action": "buy" if trade > 0 else "sell" if trade < 0 else "hold",
        }
        if name in req.prices:
            row["trade_shares"] = trade / req.prices[name]
        rows.append(row)
    gross = sum((abs(row["trade_value"]) for row in rows), ZERO)
    cash_weight = 1 - sum(req.targets.values(), ZERO)
    return {
        "total_value": total,
        "cash_target_weight": cash_weight,
        "cash_target_value": cash_weight * total,
        "positions": rows,
        "gross_trade_value": gross,
        "turnover_one_way": gross / 2 / total,
        "n_buys": sum(row["action"] == "buy" for row in rows),
        "n_sells": sum(row["action"] == "sell" for row in rows),
    }


def equal_budgets(amount: Decimal, capacities: dict[str, Decimal]) -> dict[str, Decimal]:
    """Equal cash allocations with per-name caps; leave excess cash unspent."""
    remaining = amount
    result = dict.fromkeys(capacities, ZERO)
    active = sorted(capacities)
    while active and remaining > 0:
        share = remaining / len(active)
        capped = [name for name in active if capacities[name] - result[name] <= share]
        if not capped:
            for name in active:
                allocated = min(share, remaining)
                result[name] += allocated
                remaining -= allocated
            break
        for name in capped:
            allocated = capacities[name] - result[name]
            result[name] += allocated
            remaining -= allocated
            active.remove(name)
    return result
