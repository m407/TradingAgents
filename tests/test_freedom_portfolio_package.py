"""Public API and dependency-boundary tests for tradernet_global."""

import os
import subprocess
import sys

import tradernet_global
from tradernet_global import (
    CashBalance,
    CurrencyTotal,
    FreedomPortfolioError,
    FreedomPortfolioValuation,
    PositionValuation,
    fetch_freedom_portfolio,
    value_freedom_portfolio,
)


def test_public_api_exports_models_reader_and_pure_evaluator():
    assert tradernet_global.CashBalance is CashBalance
    assert tradernet_global.CurrencyTotal is CurrencyTotal
    assert tradernet_global.FreedomPortfolioError is FreedomPortfolioError
    assert tradernet_global.FreedomPortfolioValuation is FreedomPortfolioValuation
    assert tradernet_global.PositionValuation is PositionValuation
    assert tradernet_global.fetch_freedom_portfolio is fetch_freedom_portfolio
    assert tradernet_global.value_freedom_portfolio is value_freedom_portfolio


def test_import_does_not_load_tradingagents_or_read_keys():
    script = """
import sys
import tradernet_global
assert not any(name == 'tradingagents' or name.startswith('tradingagents.') for name in sys.modules)
assert callable(tradernet_global.value_freedom_portfolio)
assert callable(tradernet_global.fetch_freedom_portfolio)
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env={**os.environ, "PYTHONPATH": ".", "PYTHON_DOTENV_DISABLED": "1"},
    )
