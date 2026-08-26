"""Validated, secret-free configuration for portfolio automation."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

PORTFOLIO_CONFIG_ENV = "TRADINGAGENTS_PORTFOLIO_CONFIG_JSON"
TRADERNET_PUBLIC_KEY_ENV = "TRADERNET_PUBLIC_KEY"
TRADERNET_PRIVATE_KEY_ENV = "TRADERNET_PRIVATE_KEY"

_STRUCTURED_OVERRIDES = {
    "TRADINGAGENTS_PORTFOLIO_WATCHLIST_JSON": "watchlist",
    "TRADINGAGENTS_PORTFOLIO_RATING_WEIGHTS_JSON": "rating_weights",
    "TRADINGAGENTS_PORTFOLIO_PROFIT_LADDER_JSON": "profit_ladder",
}
_SECRET_KEY_PARTS = ("api_key", "credential", "private_key", "public_key", "secret", "signature")


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionMode(str, Enum):
    DRY_RUN = "dry-run"
    LIVE = "live"


class AccountScope(_ConfigModel):
    account_id: str = Field(min_length=1)
    market_timezone: str = Field(min_length=1)

    @field_validator("account_id", "market_timezone")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        if not (cleaned := value.strip()):
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("market_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("must be a valid IANA timezone") from exc
        return value


class RatingWeights(_ConfigModel):
    buy: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    overweight: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    hold: Decimal = Field(ge=Decimal("0"), le=Decimal("0"))
    underweight: Decimal = Field(ge=Decimal("-1"), lt=Decimal("0"))
    sell: Decimal = Field(ge=Decimal("-1"), lt=Decimal("0"))

    @model_validator(mode="after")
    def _ordered_weights(self) -> RatingWeights:
        if not self.buy > self.overweight > self.hold > self.underweight > self.sell:
            raise ValueError(
                "rating weights must descend: buy > overweight > hold > underweight > sell"
            )
        return self


class HardRiskLimits(_ConfigModel):
    max_abs_position_weight: Decimal = Field(gt=0, le=1)
    max_gross_exposure: Decimal = Field(gt=0)
    max_abs_net_exposure: Decimal = Field(gt=0)
    max_order_notional: Decimal = Field(gt=0)
    max_position_notional: Decimal = Field(gt=0)
    min_average_daily_notional: Decimal = Field(gt=0)
    allow_short: bool
    allow_margin: bool
    permitted_instrument_types: tuple[str, ...] = Field(min_length=1)

    @field_validator("permitted_instrument_types")
    @classmethod
    def _valid_instrument_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if any(not value for value in cleaned):
            raise ValueError("instrument types must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("instrument types must be unique")
        return cleaned


class InitialStopPolicy(_ConfigModel):
    loss_fraction: Decimal = Field(gt=0, lt=1)
    order_duration: str = Field(min_length=1)


class BreakEvenPolicy(_ConfigModel):
    activation_profit_fraction: Decimal = Field(gt=0)
    estimated_entry_cost_fraction: Decimal = Field(ge=0)
    estimated_exit_cost_fraction: Decimal = Field(ge=0)
    expected_slippage_fraction: Decimal = Field(ge=0)
    buffer_fraction: Decimal = Field(ge=0)


class ProfitLadderLevel(_ConfigModel):
    profit_fraction: Decimal = Field(gt=0)
    trailing_gap_fraction: Decimal = Field(gt=0, lt=1)


class TimeoutPolicy(_ConfigModel):
    request_seconds: Decimal = Field(gt=0)
    read_attempts: int = Field(gt=0)
    order_reconciliation_seconds: Decimal = Field(gt=0)
    stop_confirmation_seconds: Decimal = Field(gt=0)
    reconnect_max_seconds: Decimal = Field(gt=0)


class StopUpdatePolicy(_ConfigModel):
    cooldown_seconds: Decimal = Field(ge=0)
    minimum_improvement: Decimal = Field(gt=0)


class ReconciliationPolicy(_ConfigModel):
    interval_seconds: Decimal = Field(gt=0)
    quote_poll_seconds: Decimal = Field(gt=0)
    maximum_quote_age_seconds: Decimal = Field(gt=0)


class PortfolioConfig(_ConfigModel):
    account_scope: AccountScope
    watchlist: tuple[str, ...]
    rating_weights: RatingWeights
    hard_risk_limits: HardRiskLimits
    initial_stop: InitialStopPolicy
    break_even: BreakEvenPolicy
    profit_ladder: tuple[ProfitLadderLevel, ...] = Field(min_length=1)
    timeouts: TimeoutPolicy
    stop_updates: StopUpdatePolicy
    reconciliation: ReconciliationPolicy
    database_path: Path
    execution_mode: ExecutionMode = ExecutionMode.DRY_RUN
    require_runtime_live_confirmation: bool = True

    @field_validator("watchlist")
    @classmethod
    def _valid_watchlist(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip().upper() for value in values)
        if any(not value for value in cleaned):
            raise ValueError("watchlist symbols must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("watchlist symbols must be unique")
        return cleaned

    @field_validator("database_path")
    @classmethod
    def _valid_database_path(cls, value: Path) -> Path:
        if not str(value).strip():
            raise ValueError("database path must not be blank")
        return value

    @model_validator(mode="after")
    def _valid_ladder_and_live_gate(self) -> PortfolioConfig:
        thresholds = [level.profit_fraction for level in self.profit_ladder]
        gaps = [level.trailing_gap_fraction for level in self.profit_ladder]
        if any(
            current <= previous
            for previous, current in zip(thresholds, thresholds[1:], strict=False)
        ):
            raise ValueError("profit ladder thresholds must be strictly increasing")
        if any(
            current > previous for previous, current in zip(gaps, gaps[1:], strict=False)
        ):
            raise ValueError("profit ladder gaps must be non-increasing")
        if self.execution_mode is ExecutionMode.LIVE and not self.require_runtime_live_confirmation:
            raise ValueError("live mode must require a separate runtime confirmation")
        return self

    def fingerprint(self) -> str:
        """Return a deterministic fingerprint of all non-secret effective settings."""
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TradernetCredentials(_ConfigModel):
    """Secret-bearing runtime values; deliberately separate from PortfolioConfig."""

    public_key: SecretStr
    private_key: SecretStr

    @field_validator("public_key", "private_key")
    @classmethod
    def _not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> TradernetCredentials:
        source = os.environ if environ is None else environ
        missing = [
            name
            for name in (TRADERNET_PUBLIC_KEY_ENV, TRADERNET_PRIVATE_KEY_ENV)
            if not source.get(name, "").strip()
        ]
        if missing:
            raise ValueError(f"missing Tradernet credential environment variable(s): {', '.join(missing)}")
        return cls(
            public_key=source[TRADERNET_PUBLIC_KEY_ENV],
            private_key=source[TRADERNET_PRIVATE_KEY_ENV],
        )


def _parse_json_environment(name: str, raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{name} contains malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def _reject_secret_fields(value: Any, path: str = PORTFOLIO_CONFIG_ENV) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise ValueError(f"{path} must not contain secret-bearing field {key!r}")
            _reject_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]")


def load_portfolio_config(environ: Mapping[str, str] | None = None) -> PortfolioConfig:
    """Load validated non-secret configuration from structured JSON environment values."""
    source = os.environ if environ is None else environ
    raw = source.get(PORTFOLIO_CONFIG_ENV, "").strip()
    if not raw:
        raise ValueError(f"{PORTFOLIO_CONFIG_ENV} is required")
    data = _parse_json_environment(PORTFOLIO_CONFIG_ENV, raw)
    if not isinstance(data, dict):
        raise ValueError(f"{PORTFOLIO_CONFIG_ENV} must contain a JSON object")

    for environment_name, field_name in _STRUCTURED_OVERRIDES.items():
        override = source.get(environment_name, "").strip()
        if override:
            data[field_name] = _parse_json_environment(environment_name, override)

    _reject_secret_fields(data)
    return PortfolioConfig.model_validate(data)
