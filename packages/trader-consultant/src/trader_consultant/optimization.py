# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.portfolio.optimization: optimal weights over a set of holdings.

Solves for the weights that optimize a named objective over the holdings' annualized mean
returns μ and sample covariance Σ (simple returns, most recent common window):

- ``min_variance``: the global minimum-variance portfolio;
- ``max_sharpe``: the tangency portfolio, maximizing (wᵀμ − r_f) / √(wᵀΣw), solved in its
  convex form min yᵀΣy s.t. (μ − r_f)ᵀy = 1, w = y / Σy (the ratio is scale-invariant, so the
  direct ratio problem is ill-posed for a solver);
- ``mean_variance``: minimum variance subject to wᵀμ = ``target_return`` (a frontier point);
- ``risk_parity``: equal risk contribution, from the convex Spinu (2013) program
  min ½*yᵀΣy − Σ (1/n)*ln yᵢ, y > 0, normalized.

Weights sum to 1; ``long_only`` (default) bounds them to [0, 1] (risk parity is long-only by
construction). The estimates need more return periods than holdings, or Σ is singular. The
constrained problems are solved with SciPy's SLSQP, so long-only and target constraints are
exact. Outputs are the weights and the resulting return, volatility, Sharpe and risk
contributions, no allocation advice. Estimated μ is noisy; results inherit that.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import minimize

from .moments import annualized_moments, return_matrix, risk_contributions

# Solver tolerances.
_SLSQP_FTOL = 1e-15
_LBFGS_TOL = {"ftol": 1e-15, "gtol": 1e-12}
Method = Literal["min_variance", "max_sharpe", "mean_variance", "risk_parity"]


class OptimizationRequest(BaseModel):
    """Input for a portfolio optimization."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    prices: dict[str, list[float]] = Field(..., description="Map of holding to price series.")
    dates: list[date] = Field(
        ..., description="Shared strictly increasing dates for every price series."
    )
    method: Method = Field(..., description="The objective to optimize.")
    periods_per_year: int = Field(252, gt=0, le=366, description="Bars per year (252 daily).")
    risk_free_annual: float | None = Field(
        None, ge=-0.05, lt=1, description="Annual risk-free rate (the 3-month bill)."
    )
    target_return: float | None = Field(
        None, description="Annualized target return (decimal) for mean_variance."
    )
    long_only: bool = Field(True, description="Constrain weights to [0, 1].")

    @model_validator(mode="after")
    def _target(self) -> OptimizationRequest:
        if not 2 <= len(self.prices) <= 50:
            raise ValueError("expected 2 to 50 assets")
        if len(self.dates) < 3 or self.dates != sorted(set(self.dates)):
            raise ValueError("dates must be unique, increasing and contain at least 3 observations")
        for name, prices in self.prices.items():
            if not name.strip() or len(prices) != len(self.dates):
                raise ValueError("every asset must have prices on the same dates")
            if any(not math.isfinite(p) or p <= 0 for p in prices):
                raise ValueError("prices must be finite and positive")
        if self.method == "mean_variance" and self.target_return is None:
            raise ValueError("mean_variance needs target_return")
        if self.method == "max_sharpe" and self.risk_free_annual is None:
            raise ValueError(
                "max_sharpe needs risk_free_annual (e.g. data.market.rates t_bill_rate)"
            )
        return self


def _risk_parity(cov: np.ndarray) -> np.ndarray:
    n = cov.shape[0]
    b = np.full(n, 1.0 / n)
    res = minimize(
        lambda y: 0.5 * y @ cov @ y - b @ np.log(y),
        np.full(n, 1.0 / n),
        jac=lambda y: cov @ y - b / y,
        bounds=[(1e-12, None)] * n,  # strictly positive (the log barrier's domain)
        method="L-BFGS-B",
        options=_LBFGS_TOL,
    )
    if not res.success:
        raise ValueError(f"risk-parity solve did not converge: {res.message}")
    return res.x / res.x.sum()


def solve_weights(
    method: str,
    mu: np.ndarray,
    cov: np.ndarray,
    *,
    risk_free: float = 0.0,
    target: float | None = None,
    long_only: bool = True,
) -> np.ndarray:
    """Optimal weights (summing to 1) for ``method``; raises ValueError if infeasible."""
    if method == "risk_parity":
        return _risk_parity(cov)
    n = len(mu)
    ones = np.ones(n)
    if method == "mean_variance":
        lo, hi = float(mu.min()), float(mu.max())
        if (long_only and not lo <= target <= hi) or (lo == hi and target != lo):
            raise ValueError(
                f"target_return {target:.4f} is unreachable: holdings' expected returns span "
                f"{lo:.4f} to {hi:.4f}" + (" (long-only)" if long_only else "")
            )
    if method == "max_sharpe":
        excess = mu - risk_free
        if long_only and not (excess > 0).any():
            raise ValueError("no holding's expected return exceeds the risk-free rate")
        # y is unnormalized; w = y / Σy afterwards
        cons = [{"type": "eq", "fun": lambda y: y @ excess - 1, "jac": lambda y: excess}]
        start = np.clip(excess, 0, None) if long_only else excess
        start = start / (start @ excess)
        bounds = [(0.0, None)] * n if long_only else None
    else:
        cons = [{"type": "eq", "fun": lambda w: w.sum() - 1, "jac": lambda w: ones}]
        if method == "mean_variance":
            cons.append({"type": "eq", "fun": lambda w: w @ mu - target, "jac": lambda w: mu})
        start = ones / n
        bounds = [(0.0, 1.0)] * n if long_only else None
    res = minimize(
        lambda w: w @ cov @ w,
        start,
        jac=lambda w: 2 * cov @ w,
        bounds=bounds,
        constraints=cons,
        method="SLSQP",
        options={"ftol": _SLSQP_FTOL, "maxiter": 1000},
    )
    if not res.success:
        raise ValueError(f"no {method} solution under these constraints: {res.message}")
    if method != "max_sharpe":
        return res.x
    total = res.x.sum()
    if total <= 0:  # no tangency on the frontier's upper branch at this r_f
        raise ValueError(
            "no finite max-Sharpe portfolio at this risk-free rate (expected returns below "
            "r_f, or a riskless long/short combination); try long_only or another method"
        )
    return res.x / total


def compute_optimization(req: OptimizationRequest) -> dict[str, Any]:
    names, r = return_matrix(req.prices)
    if r.shape[1] <= len(names):
        raise ValueError(
            f"{r.shape[1]} return periods for {len(names)} holdings: the covariance is singular; "
            "use a longer price history (more periods than holdings)"
        )
    mu, cov = annualized_moments(r, req.periods_per_year)
    if not np.isfinite(mu).all() or not np.isfinite(cov).all():
        raise ValueError("non-finite estimated moments")
    if np.linalg.eigvalsh(cov).min() <= 1e-12:
        raise ValueError("degenerate covariance: provide independent, varying price histories")
    w = solve_weights(
        req.method,
        mu,
        cov,
        risk_free=req.risk_free_annual or 0.0,
        target=req.target_return,
        long_only=req.long_only,
    )
    if not np.isfinite(w).all() or abs(float(w.sum()) - 1) > 1e-7:
        raise ValueError("solver returned invalid weights")
    if req.long_only and (w.min() < -1e-7 or w.max() > 1 + 1e-7):
        raise ValueError("solver violated long-only bounds")
    if req.method == "mean_variance" and abs(float(w @ mu) - req.target_return) > 1e-7:
        raise ValueError("solver violated target return")
    w = w + 0.0  # −0.0 to 0.0
    vol = math.sqrt(max(float(w @ cov @ w), 0.0))
    ret = float(w @ mu)
    rc = risk_contributions(w, cov)
    rf = req.risk_free_annual
    return {
        "method": req.method,
        "long_only": req.long_only,
        "weights": {k: float(w[i]) for i, k in enumerate(names)},
        "gross_exposure": float(np.abs(w).sum()),
        "annual_return": ret,
        "annual_volatility": vol,
        "sharpe": (ret - rf) / vol if rf is not None and vol else None,
        "risk_contributions": None
        if rc is None
        else {k: float(rc[i]) for i, k in enumerate(names)},
        "risk_free_annual": req.risk_free_annual,
        "target_return": req.target_return,
        "n_periods": r.shape[1],
    }
