"""Deterministic validation and conversion of an LLM proposal to a reviewable draft."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from uuid import uuid4

from .models import (
    Allocation,
    AllocationProposal,
    ConsultationRequest,
    Issue,
    OrderDraft,
    Trade,
)
from .rebalance import ZERO, RebalanceRequest, compute_rebalance, equal_budgets


def issue(code: str, detail: str, ticker: str | None = None) -> Issue:
    return Issue(code=code, detail=detail, ticker=ticker)


def nav(req: ConsultationRequest) -> Decimal:
    return sum((p.price * p.quantity for p in req.snapshot.positions), req.snapshot.free_cash)


def floor_step(quantity: Decimal, step: Decimal) -> Decimal:
    return (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step


def minimum_quantity(price: Decimal, step: Decimal, minimum: Decimal) -> Decimal:
    return (minimum / price / step).to_integral_value(rounding=ROUND_CEILING) * step


def validate_request(req: ConsultationRequest, now: datetime) -> tuple[list[Issue], list[Issue]]:
    snapshot, policy = req.snapshot, req.policy
    errors, warnings = [], []
    if now.tzinfo is None or now.utcoffset() is None:
        return [issue("invalid_clock", "Clock must return a timezone-aware datetime.")], []
    age = (now - snapshot.as_of).total_seconds()
    if age < 0 or age >= policy.max_snapshot_age_seconds:
        errors.append(issue("snapshot_age", "Snapshot is stale or future-dated."))
    if not snapshot.complete:
        errors.append(issue("incomplete_snapshot", "Managed portfolio data is incomplete."))
    if snapshot.has_active_orders:
        errors.append(issue("active_orders", "Cancel or settle active orders before consulting."))
    positions = {p.ticker: p for p in snapshot.positions}
    actions = [r.ticker for r in req.recommendations]
    if len(actions) != len(set(actions)):
        errors.append(issue("duplicate_recommendation", "Each ticker needs exactly one action."))
    for ticker in sorted(set(actions) - positions.keys()):
        errors.append(issue("unknown_instrument", "Missing position or candidate data.", ticker))
    for ticker in sorted(positions.keys() - policy.max_weights.keys()):
        errors.append(
            issue("missing_maximum_weight", "External maximum weight is required.", ticker)
        )
    total = nav(req)
    if total <= 0:
        errors.append(issue("empty_managed_portfolio", "Managed NAV must be positive."))
    if snapshot.free_cash < policy.cash_reserve:
        errors.append(issue("cash_reserve", "Existing free cash is below the required reserve."))
    for p in snapshot.positions:
        age = (now - p.price_at).total_seconds()
        if age < 0 or age >= policy.max_price_age_seconds:
            errors.append(issue("price_age", "Reference price is stale or future-dated.", p.ticker))
        if (
            total > 0
            and p.ticker in policy.max_weights
            and p.quantity * p.price > policy.max_weights[p.ticker] * total
        ):
            warnings.append(
                issue(
                    "existing_overweight",
                    "Existing overweight may only stay or decrease.",
                    p.ticker,
                )
            )
    if errors:
        return errors, warnings
    buy_minimum = ZERO
    for rec in req.recommendations:
        p = positions[rec.ticker]
        minimum = minimum_quantity(p.price, p.quantity_step, policy.minimum_trade)
        if rec.action == "BUY":
            capacity = policy.max_weights[p.ticker] * total - p.quantity * p.price
            if capacity < minimum * p.price:
                errors.append(
                    issue("buy_capacity", "No mandatory minimum BUY fits the weight cap.", p.ticker)
                )
            buy_minimum += (
                minimum * p.price * (1 + policy.slippage_rate) * (1 + policy.commission_rate)
            )
        if rec.action == "SELL" and minimum > p.available_quantity:
            errors.append(
                issue(
                    "sell_capacity",
                    "No mandatory minimum SELL fits the available quantity.",
                    p.ticker,
                )
            )
    if buy_minimum > min(policy.buy_budget, snapshot.free_cash - policy.cash_reserve):
        errors.append(
            issue(
                "minimum_buy_budget",
                "Available cash/budget cannot fund every mandatory BUY including costs.",
            )
        )
    return errors, warnings


def build_draft(
    req: ConsultationRequest,
    proposal: AllocationProposal,
    now: datetime,
) -> tuple[OrderDraft | None, list[Issue]]:
    errors, warnings = validate_request(req, now)
    if errors:
        return None, errors
    snapshot, policy = req.snapshot, req.policy
    positions = {p.ticker: p for p in snapshot.positions}
    recommendations = {r.ticker: r for r in req.recommendations}
    buys = {r.ticker for r in req.recommendations if r.action == "BUY"}
    sells = {r.ticker for r in req.recommendations if r.action == "SELL"}
    total = nav(req)
    if set(proposal.sell_target_weights) != sells:
        errors.append(
            issue("sell_targets", "Provide exactly the SELL tickers as sell_target_weights.")
        )
    if set(proposal.reasons) != set(recommendations) or any(
        not s.strip() for s in proposal.reasons.values()
    ):
        errors.append(
            issue("reasons", "Provide a nonempty reason for every recommendation, including KEEP.")
        )
    if (not buys and proposal.purchase_amount != 0) or (buys and proposal.purchase_amount <= 0):
        errors.append(
            issue(
                "purchase_amount", "A positive purchase amount is required exactly when BUY exists."
            )
        )
    buy_factor = (1 + policy.slippage_rate) * (1 + policy.commission_rate)
    if proposal.purchase_amount * buy_factor > min(
        policy.buy_budget, snapshot.free_cash - policy.cash_reserve
    ):
        errors.append(
            issue(
                "purchase_budget",
                "Chosen purchase amount including costs exceeds budget or free cash reserve.",
            )
        )
    if errors:
        return None, errors

    holdings = {name: p.quantity * p.price for name, p in positions.items()}
    targets = {name: value / total for name, value in holdings.items()}
    capacities = {
        name: max(ZERO, policy.max_weights[name] * total - holdings[name]) for name in buys
    }
    budgets = equal_budgets(proposal.purchase_amount, capacities)
    for name, budget in budgets.items():
        targets[name] = (holdings[name] + budget) / total
    for name, weight in proposal.sell_target_weights.items():
        if weight >= targets[name]:
            errors.append(
                issue(
                    "sell_direction", "SELL target must be strictly below the current weight.", name
                )
            )
        targets[name] = weight
    if errors:
        return None, errors

    ideal = compute_rebalance(
        RebalanceRequest(
            holdings=holdings,
            targets=targets,
            cash=snapshot.free_cash,
            prices={name: p.price for name, p in positions.items()},
        )
    )
    ideals = {row["name"]: row for row in ideal["positions"]}
    quantities = {name: p.quantity for name, p in positions.items()}
    trades = []
    for name in sorted(buys | sells):
        p, rec = positions[name], recommendations[name]
        # BUY uses its equal budget directly to avoid division round-off changing a whole lot.
        delta = budgets[name] / p.price if name in buys else -ideals[name]["trade_value"] / p.price
        quantity = floor_step(delta, p.quantity_step)
        notional = quantity * p.price
        if quantity <= 0 or notional < policy.minimum_trade:
            errors.append(
                issue(
                    "mandatory_trade",
                    "Rounded trade is zero or below the minimum trade value.",
                    name,
                )
            )
            continue
        if rec.action == "SELL" and quantity > p.available_quantity:
            errors.append(issue("available_quantity", "Sale exceeds available shares.", name))
            continue
        if rec.action == "BUY":
            quantities[name] += quantity
        else:
            quantities[name] -= quantity
        if quantities[name] * p.price > max(holdings[name], policy.max_weights[name] * total):
            errors.append(
                issue("weight_cap", "Trade increases a position beyond its allowed cap.", name)
            )
        execution_price = p.price * (
            1 + policy.slippage_rate if rec.action == "BUY" else 1 - policy.slippage_rate
        )
        execution_notional = quantity * execution_price
        trades.append(
            Trade(
                ticker=name,
                side=rec.action,
                quantity=quantity,
                reference_price=p.price,
                estimated_execution_price=execution_price,
                reference_notional=notional,
                estimated_notional=execution_notional,
                estimated_fee=execution_notional * policy.commission_rate,
                reason=proposal.reasons[name],
            )
        )
    buy_outlay = sum(
        (t.estimated_notional + t.estimated_fee for t in trades if t.side == "BUY"), ZERO
    )
    # Each sale pays its own fee; none of its proceeds finance purchases.
    reserved = buy_outlay
    if buy_outlay > policy.buy_budget or reserved > snapshot.free_cash - policy.cash_reserve:
        errors.append(
            issue(
                "funding",
                "Current cash must cover purchases including buy fees without sale proceeds.",
            )
        )
    if errors:
        return None, errors
    allocations = tuple(
        Allocation(
            ticker=name,
            current_quantity=p.quantity,
            resulting_quantity=quantities[name],
            current_weight=holdings[name] / total,
            target_weight=targets[name],
            achieved_weight=quantities[name] * p.price / total,
            maximum_weight=policy.max_weights[name],
            reason=proposal.reasons.get(name, "No recommendation; quantity preserved."),
        )
        for name, p in sorted(positions.items())
    )
    expiry = min(
        now + timedelta(seconds=policy.order_ttl_seconds),
        snapshot.as_of + timedelta(seconds=policy.max_snapshot_age_seconds),
        *(p.price_at + timedelta(seconds=policy.max_price_age_seconds) for p in snapshot.positions),
    )
    cash_after = (
        snapshot.free_cash
        - reserved
        + sum(
            (t.estimated_notional - t.estimated_fee for t in trades if t.side == "SELL"),
            ZERO,
        )
    )
    spent = sum((t.reference_notional for t in trades if t.side == "BUY"), ZERO)
    return OrderDraft(
        order_id=str(uuid4()),
        snapshot_id=snapshot.snapshot_id,
        input_fingerprint=hashlib.sha256(req.model_dump_json().encode()).hexdigest(),
        created_at=now,
        expires_at=expiry,
        currency=snapshot.currency,
        weight_basis_nav=total,
        trades=tuple(trades),
        allocations=allocations,
        selected_purchase_amount=proposal.purchase_amount,
        unused_purchase_amount=proposal.purchase_amount - spent,
        estimated_cash_after=cash_after,
        cash_reserved_for_buys_and_fees=reserved,
        explanation=proposal.explanation,
        warnings=tuple(warnings),
        checks=(
            "fresh_complete_snapshot",
            "no_active_orders",
            "mandatory_directions",
            "external_weight_caps",
            "cash_reserve",
            "buy_budget",
            "lot_steps",
            "minimum_trades",
            "available_quantities",
            "no_sale_proceeds_funding",
        ),
        excluded_assets=snapshot.excluded_assets,
    ), []
