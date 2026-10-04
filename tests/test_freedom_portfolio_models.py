"""Offline tests for independent Freedom portfolio result models."""

from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal

from tradernet_global.models import (
    CashBalance,
    CurrencyTotal,
    FreedomPortfolioError,
    FreedomPortfolioValuation,
    PositionValuation,
)


def test_models_preserve_decimal_provenance_and_times():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    position = PositionValuation(
        0, "p1", "ABC.US_RP1", "Synthetic", 8, "USD", Decimal("2"),
        Decimal("10"), Decimal("100"), Decimal("0"), Decimal("20"),
        "repo", "portfolio", now, None, ("synthetic warning",),
    )
    cash = CashBalance(0, "USD", Decimal("1.25"))
    total = CurrencyTotal("USD", Decimal("20"), Decimal("1.25"),
                          Decimal("21.25"), Decimal("21.25"), True)
    valuation = FreedomPortfolioValuation((position,), (cash,), {"USD": total},
                                          True, now, now)

    assert position.value == Decimal("20")
    assert position.price_source == "portfolio"
    assert position.source_time == now
    assert position.reason is None
    assert position.warnings == ("synthetic warning",)
    assert cash.amount == Decimal("1.25")
    assert valuation.totals_by_currency["USD"].total == Decimal("21.25")
    assert valuation.fetched_at.tzinfo is timezone.utc
    assert {field.name for field in fields(PositionValuation)} >= {
        "quantity", "price", "factor", "accrued_interest", "value",
        "reason", "price_source", "source_time", "warnings",
    }


def test_unavailable_reasons_are_safe_codes():
    err = FreedomPortfolioError("portfolio_unavailable")
    assert err.code == "portfolio_unavailable"
    assert str(err) == "portfolio_unavailable"


def test_ambiguous_source_time_can_be_retained_without_claiming_timezone():
    position = PositionValuation(
        0, "p1", "ABC.US", None, 1, "USD", Decimal("1"), Decimal("10"),
        Decimal("100"), Decimal("0"), Decimal("10"), "quote", "quote",
        source_time_raw="2026-10-02 12:30:00",
    )

    assert position.source_time is None
    assert position.source_time_raw == "2026-10-02 12:30:00"


def test_package_import_does_not_load_tradingagents():
    import subprocess
    import sys
    import textwrap

    # Pytest plugins/project conftests may already load the application package;
    # verify the import boundary in a clean interpreter instead.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import sys
            import tradernet_global
            assert tradernet_global.__file__
            assert not any(name == 'tradingagents' or name.startswith('tradingagents.')
                           for name in sys.modules)
        """)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
