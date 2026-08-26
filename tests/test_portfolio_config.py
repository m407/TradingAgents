from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from tradingagents.portfolio.config import (
    PORTFOLIO_CONFIG_ENV,
    ExecutionMode,
    PortfolioConfig,
    TradernetCredentials,
    load_portfolio_config,
)


@pytest.fixture
def policy() -> dict:
    return {
        "account_scope": {"account_id": "portfolio-a", "market_timezone": "Europe/Prague"},
        "watchlist": ["aapl", "MSFT"],
        "rating_weights": {
            "buy": "0.20",
            "overweight": "0.10",
            "hold": "0",
            "underweight": "-0.10",
            "sell": "-0.20",
        },
        "hard_risk_limits": {
            "max_abs_position_weight": "0.20",
            "max_gross_exposure": "1.20",
            "max_abs_net_exposure": "0.80",
            "max_order_notional": "1000",
            "max_position_notional": "5000",
            "min_average_daily_notional": "100000",
            "allow_short": True,
            "allow_margin": False,
            "permitted_instrument_types": ["stock", "etf"],
        },
        "initial_stop": {"loss_fraction": "0.03", "order_duration": "good-till-cancelled"},
        "break_even": {
            "activation_profit_fraction": "0.04",
            "estimated_entry_cost_fraction": "0.001",
            "estimated_exit_cost_fraction": "0.001",
            "expected_slippage_fraction": "0.002",
            "buffer_fraction": "0.001",
        },
        "profit_ladder": [
            {"profit_fraction": "0.05", "trailing_gap_fraction": "0.03"},
            {"profit_fraction": "0.10", "trailing_gap_fraction": "0.02"},
        ],
        "timeouts": {
            "request_seconds": "10",
            "read_attempts": 3,
            "order_reconciliation_seconds": "30",
            "stop_confirmation_seconds": "15",
            "reconnect_max_seconds": "60",
        },
        "stop_updates": {"cooldown_seconds": "5", "minimum_improvement": "0.01"},
        "reconciliation": {
            "interval_seconds": "30",
            "quote_poll_seconds": "5",
            "maximum_quote_age_seconds": "10",
        },
        "database_path": "var/portfolio.sqlite3",
    }


def test_complete_policy_is_validated_and_defaults_to_dry_run(policy):
    config = PortfolioConfig.model_validate(policy)

    assert config.execution_mode is ExecutionMode.DRY_RUN
    assert config.watchlist == ("AAPL", "MSFT")
    assert str(config.rating_weights.buy) == "0.20"


def test_no_numeric_risk_or_stop_policy_is_defaulted(policy):
    for field in ("hard_risk_limits", "initial_stop", "break_even", "profit_ladder"):
        incomplete = deepcopy(policy)
        incomplete.pop(field)
        with pytest.raises(ValidationError, match=field):
            PortfolioConfig.model_validate(incomplete)


def test_ladder_must_tighten_as_thresholds_increase(policy):
    policy["profit_ladder"][1]["trailing_gap_fraction"] = "0.04"

    with pytest.raises(ValidationError, match="non-increasing"):
        PortfolioConfig.model_validate(policy)


def test_live_mode_cannot_disable_second_gate(policy):
    policy.update(execution_mode="live", require_runtime_live_confirmation=False)

    with pytest.raises(ValidationError, match="separate runtime confirmation"):
        PortfolioConfig.model_validate(policy)


def test_environment_loader_reports_malformed_json():
    with pytest.raises(ValueError, match=rf"{PORTFOLIO_CONFIG_ENV}.*line 1, column 2"):
        load_portfolio_config({PORTFOLIO_CONFIG_ENV: "{"})


def test_structured_environment_override_is_validated(policy):
    config = load_portfolio_config({
        PORTFOLIO_CONFIG_ENV: json.dumps(policy),
        "TRADINGAGENTS_PORTFOLIO_WATCHLIST_JSON": '["nvda"]',
    })

    assert config.watchlist == ("NVDA",)


def test_non_secret_config_rejects_credentials(policy):
    policy["private_key"] = "do-not-accept"

    with pytest.raises(ValueError, match="secret-bearing field 'private_key'"):
        load_portfolio_config({PORTFOLIO_CONFIG_ENV: json.dumps(policy)})


def test_credentials_are_environment_only_and_secret_in_repr():
    credentials = TradernetCredentials.from_environment({
        "TRADERNET_PUBLIC_KEY": "public-value",
        "TRADERNET_PRIVATE_KEY": "private-value",
    })

    assert "public-value" not in repr(credentials)
    assert "private-value" not in repr(credentials)
    with pytest.raises(ValueError, match="TRADERNET_PRIVATE_KEY"):
        TradernetCredentials.from_environment({"TRADERNET_PUBLIC_KEY": "present"})


def test_fingerprint_is_deterministic_and_cannot_include_credentials(policy):
    first = PortfolioConfig.model_validate(policy)
    reordered = {key: policy[key] for key in reversed(policy)}
    second = PortfolioConfig.model_validate(reordered)

    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 64
    assert "private" not in first.model_dump_json().lower()
