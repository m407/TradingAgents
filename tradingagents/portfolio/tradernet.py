"""Validated, secret-safe boundary around the optional Tradernet SDK."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from tradingagents.portfolio.config import TradernetCredentials
from tradingagents.portfolio.models import (
    Balance,
    Fill,
    MarketSession,
    MarketState,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Position,
    Quote,
    StopState,
)

_REDACTED = "<redacted>"
_SENSITIVE_KEYS = {
    "account",
    "accountid",
    "account_id",
    "apikey",
    "api_key",
    "authorization",
    "cookie",
    "privatekey",
    "private_key",
    "publickey",
    "public_key",
    "secret",
    "secretkey",
    "secret_key",
    "signature",
    "token",
}
_ORDER_STATUS = {
    "new": "active",
    "pending": "pending",
    "submitted": "pending",
    "accepted": "active",
    "active": "active",
    "open": "active",
    "partially_filled": "partially-filled",
    "partially-filled": "partially-filled",
    "partial": "partially-filled",
    "part_filled": "partially-filled",
    "filled": "filled",
    "executed": "filled",
    "done": "filled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "rejected": "rejected",
    "expired": "expired",
}
_ACTIVE_ORDER_STATUSES = {
    OrderStatus.PENDING,
    OrderStatus.ACTIVE,
    OrderStatus.PARTIALLY_FILLED,
}


class TradernetAdapterError(RuntimeError):
    """A classified and sanitized broker-boundary failure."""

    def __init__(
        self,
        category: str,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        unknown_outcome: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.retryable = retryable
        self.unknown_outcome = unknown_outcome


class TradernetDependencyError(TradernetAdapterError):
    """The optional Tradernet SDK is not installed."""


def redact_diagnostics(value: Any, sensitive_values: Sequence[str] = ()) -> Any:
    """Recursively remove authentication material and configured identifiers."""

    secrets = tuple(
        item for item in sensitive_values if isinstance(item, str) and item
    )
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if normalized in _SENSITIVE_KEYS or compact in _SENSITIVE_KEYS:
                redacted[key] = _REDACTED
            else:
                redacted[key] = redact_diagnostics(item, secrets)
        return redacted
    if isinstance(value, list):
        return [redact_diagnostics(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_diagnostics(item, secrets) for item in value)
    if isinstance(value, set):
        return {redact_diagnostics(item, secrets) for item in value}
    if isinstance(value, str):
        result = value
        for secret in secrets:
            result = result.replace(secret, _REDACTED)
        return result
    return value


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TradernetAdapterError("validation", f"{context} must be an object")
    return value


def _first(data: Mapping[str, Any], *keys: str, required: bool = True) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    if required:
        raise TradernetAdapterError("validation", f"missing required field: {keys[0]}")
    return None


def _text(data: Mapping[str, Any], *keys: str, required: bool = True) -> str | None:
    value = _first(data, *keys, required=required)
    if value is None:
        return None
    if not isinstance(value, (str, int)) or not str(value).strip():
        raise TradernetAdapterError("validation", f"invalid field: {keys[0]}")
    return str(value).strip()


def _decimal(
    data: Mapping[str, Any], *keys: str, required: bool = True
) -> Decimal | None:
    value = _first(data, *keys, required=required)
    if value is None:
        return None
    if isinstance(value, bool):
        raise TradernetAdapterError("validation", f"invalid decimal field: {keys[0]}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TradernetAdapterError(
            "validation", f"invalid decimal field: {keys[0]}"
        ) from exc
    if not parsed.is_finite():
        raise TradernetAdapterError("validation", f"non-finite field: {keys[0]}")
    return parsed


def _timestamp(
    data: Mapping[str, Any], *keys: str, required: bool = True
) -> datetime | None:
    value = _first(data, *keys, required=required)
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TradernetAdapterError(
                "validation", f"invalid timestamp field: {keys[0]}"
            ) from exc
    else:
        raise TradernetAdapterError("validation", f"invalid timestamp field: {keys[0]}")
    if parsed.tzinfo is None:
        raise TradernetAdapterError("validation", f"timezone missing from field: {keys[0]}")
    return parsed.astimezone(timezone.utc)


def _items(payload: Any, *keys: str) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        raw_items = payload
    else:
        data = _mapping(payload, "response")
        raw_items = None
        for key in keys:
            if key in data:
                raw_items = data[key]
                break
        if raw_items is None:
            raise TradernetAdapterError("validation", f"missing response collection: {keys[0]}")
    if not isinstance(raw_items, list):
        raise TradernetAdapterError("validation", f"{keys[0]} must be a list")
    return [_mapping(item, f"{keys[0]} item") for item in raw_items]


def _side_and_quantity(data: Mapping[str, Any]) -> tuple[str, Decimal]:
    quantity = _decimal(data, "quantity", "qty", "count", "q")
    assert quantity is not None
    side = _text(data, "side", "action", required=False)
    normalized_side = side.lower() if side else ("buy" if quantity > 0 else "sell")
    side_aliases = {"b": "buy", "buy": "buy", "s": "sell", "sell": "sell"}
    if normalized_side not in side_aliases:
        raise TradernetAdapterError("validation", f"invalid order side: {side}")
    normalized_side = side_aliases[normalized_side]
    if quantity == 0:
        raise TradernetAdapterError("validation", "order quantity cannot be zero")
    if quantity < 0 and normalized_side == "buy":
        raise TradernetAdapterError("validation", "order side contradicts signed quantity")
    signed = abs(quantity) if normalized_side == "buy" else -abs(quantity)
    return normalized_side, signed


class TradernetAdapter:
    """Duck-typed Tradernet client with strict project-owned contracts."""

    def __init__(
        self,
        credentials: TradernetCredentials,
        *,
        client: Any | None = None,
        sdk_factory: Callable[..., Any] | None = None,
        timeout: float = 10.0,
        read_attempts: int = 3,
        retry_backoff: float = 0.25,
        sensitive_identifiers: Sequence[str] = (),
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout <= 0 or read_attempts < 1 or retry_backoff < 0:
            raise ValueError("timeout and read retry policy must be positive and bounded")
        self.credentials = credentials
        self.timeout = timeout
        self.read_attempts = read_attempts
        self.retry_backoff = retry_backoff
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        public_key = credentials.public_key.get_secret_value()
        private_key = credentials.private_key.get_secret_value()
        self._sensitive = (
            public_key,
            private_key,
            *sensitive_identifiers,
        )
        self.client = client or self._build_client(sdk_factory)

    def _build_client(self, factory: Callable[..., Any] | None) -> Any:
        if factory is None:
            factory = self._load_sdk_factory()
        public = self.credentials.public_key.get_secret_value()
        private = self.credentials.private_key.get_secret_value()
        attempts = (
            {"api_key": public, "secret_key": private},
            {"public_key": public, "private_key": private},
            {"public": public, "private": private},
        )
        for index, kwargs in enumerate(attempts):
            try:
                return factory(**kwargs)
            except TypeError:
                if index + 1 == len(attempts):
                    raise
        raise AssertionError("unreachable")

    @staticmethod
    def _load_sdk_factory() -> Callable[..., Any]:
        candidates = (
            ("tradernet", ("Tradernet", "TraderNetAPI")),
            ("tradernet_sdk", ("API", "TraderNetAPI", "TraderNet")),
            ("tradernet_api.api", ("API",)),
        )
        for module_name, names in candidates:
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            for name in names:
                factory = getattr(module, name, None)
                if callable(factory):
                    return factory
        raise TradernetDependencyError(
            "dependency",
            'Tradernet support requires the optional SDK; install it with '
            '`pip install "tradingagents[tradernet]"`.',
        )

    def sanitize(self, value: Any) -> Any:
        return redact_diagnostics(value, self._sensitive)

    def _method(self, names: Sequence[str]) -> Callable[..., Any]:
        for name in names:
            method = getattr(self.client, name, None)
            if callable(method):
                return method
        raise TradernetAdapterError(
            "validation", f"Tradernet SDK does not implement any of: {', '.join(names)}"
        )

    def _call(self, names: Sequence[str], kwargs: Mapping[str, Any]) -> Any:
        method = self._method(names)
        call_kwargs = dict(kwargs)
        method_name = getattr(method, "__name__", "")
        if method_name == "account_summary":
            call_kwargs = {}
        elif method_name == "get_quotes":
            call_kwargs = {"symbols": [call_kwargs.pop("ticker")]}
        elif method_name == "security_info":
            call_kwargs = {"symbol": call_kwargs.pop("ticker")}
        elif method_name == "get_placed":
            call_kwargs = {"active": call_kwargs.pop("active_only")}
        elif method_name == "get_historical":
            now = self._clock().astimezone(timezone.utc)
            call_kwargs = {"start": now - timedelta(days=365), "end": now}
        elif method_name == "get_trades_history":
            now = self._clock().astimezone(timezone.utc)
            call_kwargs = {"start": now.date() - timedelta(days=365), "end": now.date()}
        elif method_name == "place_order":
            identity = str(call_kwargs.pop("client_order_id"))
            call_kwargs = {
                "symbol": call_kwargs.pop("ticker"),
                "quantity": call_kwargs.pop("count")
                * (1 if call_kwargs.pop("side") == "buy" else -1),
                "price": call_kwargs.pop("limit_price", 0),
                "duration": call_kwargs.pop("order_exp"),
                "use_margin": call_kwargs.pop("margin"),
                "custom_order_id": int(hashlib.sha256(identity.encode()).hexdigest()[:12], 16),
            }
        elif method_name == "cancel":
            call_kwargs = {"order_id": int(call_kwargs["order_id"])}
        elif method_name == "stop":
            call_kwargs = {
                "symbol": call_kwargs["ticker"],
                "price": float(call_kwargs["stop_loss"]),
            }
        elif method_name == "trailing_stop":
            call_kwargs = {
                "symbol": call_kwargs["ticker"],
                "percent": float(call_kwargs["trailing_percent"]),
            }
        elif method_name == "get_candles":
            call_kwargs = {
                "symbol": call_kwargs["ticker"],
                "start": datetime.fromisoformat(call_kwargs["start_date"]),
                "end": datetime.fromisoformat(call_kwargs["end_date"]),
                "timeframe": 86400 if call_kwargs["interval"] == "day" else int(call_kwargs["interval"]),
            }
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            accepts_kwargs = any(
                item.kind == inspect.Parameter.VAR_KEYWORD
                for item in signature.parameters.values()
            )
            if "timeout" in signature.parameters or accepts_kwargs:
                call_kwargs.setdefault("timeout", self.timeout)
        return method(**call_kwargs)

    def _response(self, response: Any, *, mutation: bool) -> Any:
        if callable(getattr(response, "raise_for_status", None)):
            response.raise_for_status()
        if callable(getattr(response, "json", None)):
            response = response.json()
        if isinstance(response, Mapping):
            error_value = next(
                (response[key] for key in ("error", "errorMsg", "errMsg") if response.get(key)),
                None,
            )
            if error_value is not None:
                code = _text(response, "errorCode", "code", "status", required=False)
                message = str(self.sanitize(str(error_value)))
                lowered = message.lower()
                if code in {"401", "403"} or any(
                    marker in lowered for marker in ("auth", "credential", "forbidden")
                ):
                    category = "authorization"
                elif any(marker in lowered for marker in ("invalid", "validation", "required")):
                    category = "validation"
                else:
                    category = "rejection" if mutation else "unknown"
                raise TradernetAdapterError(category, message, code=code)
            if "result" in response:
                return response["result"]
        return response

    def _classify_exception(self, exc: Exception, *, mutation: bool) -> TradernetAdapterError:
        response = getattr(exc, "response", None)
        status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
        message = str(self.sanitize(str(exc))) or type(exc).__name__
        if status is not None:
            code = str(status)
            if status in {401, 403}:
                return TradernetAdapterError("authorization", message, code=code)
            if status in {400, 404, 405, 422}:
                category = "rejection" if mutation else "validation"
                return TradernetAdapterError(category, message, code=code)
            if mutation and (status == 429 or status >= 500):
                return TradernetAdapterError(
                    "unknown-outcome",
                    f"Tradernet mutation outcome is unknown: {message}",
                    code=code,
                    unknown_outcome=True,
                )
            return TradernetAdapterError(
                "http", message, code=code, retryable=status == 429 or status >= 500
            )
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            if mutation:
                return TradernetAdapterError(
                    "unknown-outcome",
                    f"Tradernet mutation outcome is unknown: {message}",
                    unknown_outcome=True,
                )
            return TradernetAdapterError("transport", message, retryable=True)
        return TradernetAdapterError("unknown", message)

    def _read(self, names: Sequence[str], **kwargs: Any) -> Any:
        last_error: TradernetAdapterError | None = None
        for attempt in range(self.read_attempts):
            try:
                return self._response(self._call(names, kwargs), mutation=False)
            except TradernetAdapterError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
            except Exception as exc:
                classified = self._classify_exception(exc, mutation=False)
                if not classified.retryable:
                    raise classified from exc
                last_error = classified
            if attempt + 1 < self.read_attempts:
                self._sleep(self.retry_backoff * (2**attempt))
        assert last_error is not None
        raise last_error

    def _mutate(self, names: Sequence[str], **kwargs: Any) -> Any:
        try:
            return self._response(self._call(names, kwargs), mutation=True)
        except TradernetAdapterError:
            raise
        except Exception as exc:
            raise self._classify_exception(exc, mutation=True) from exc

    def get_portfolio(self) -> Any:
        response = self._read(
            ("get_portfolio", "portfolio", "get_portfolio_info", "account_summary")
        )
        data = _mapping(response, "portfolio response")
        positions = tuple(self._parse_position(item) for item in _items(data, "positions", "portfolio"))
        balance_items = _items(data, "balances", "currencies")
        balances = tuple(self._parse_balance(item) for item in balance_items)
        as_of = _timestamp(data, "as_of", "timestamp", "date")
        assert as_of is not None
        return PortfolioSnapshot(balances=balances, positions=positions, as_of=as_of)

    def reconcile_account(self) -> PortfolioSnapshot:
        return self.get_portfolio()

    def _parse_balance(self, data: Mapping[str, Any]) -> Any:
        currency = _text(data, "currency", "curr")
        cash = _decimal(data, "cash", "balance")
        available = _decimal(data, "available", "available_cash")
        equity = _decimal(data, "equity", "value")
        assert currency is not None and cash is not None and available is not None and equity is not None
        if available > equity and equity >= 0:
            raise TradernetAdapterError("validation", "available balance exceeds equity")
        return Balance(currency=currency, cash=cash, available=available, equity=equity)

    def _parse_position(self, data: Mapping[str, Any]) -> Any:
        position_id = _text(data, "position_id", "id", "positionId")
        symbol = _text(data, "symbol", "ticker", "instr")
        quantity = _decimal(data, "quantity", "qty", "q")
        average_price = _decimal(data, "average_price", "avg_price", "price")
        market_value = _decimal(data, "market_value", "value", "amount")
        currency = _text(data, "currency", "curr")
        unrealized_pnl = _decimal(data, "unrealized_pnl", "profit", "pnl")
        side = _text(data, "side", "direction", required=False)
        assert quantity is not None and average_price is not None and market_value is not None
        if quantity == 0 or average_price <= 0:
            raise TradernetAdapterError("validation", "open position has invalid quantity or price")
        if side and ((side.lower() in {"long", "buy"}) != (quantity > 0)):
            raise TradernetAdapterError("validation", "position side contradicts signed quantity")
        return Position(
            position_id=position_id,
            symbol=symbol,
            quantity=quantity,
            average_price=average_price,
            market_value=market_value,
            currency=currency,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=_decimal(data, "realized_pnl", "realized_profit", required=False),
        )

    def get_quote(self, symbol: str) -> Any:
        response = self._read(
            ("get_quote", "get_ticker_info", "quote", "get_quotes"), ticker=symbol
        )
        data = _mapping(response, "quote response")
        if "quote" in data:
            data = _mapping(data["quote"], "quote")
        returned_symbol = _text(data, "symbol", "ticker", "instr")
        bid = _decimal(data, "bid", "bid_price")
        ask = _decimal(data, "ask", "ask_price")
        last = _decimal(data, "last", "last_price", "price", required=False)
        as_of = _timestamp(data, "as_of", "timestamp", "date")
        assert returned_symbol is not None and bid is not None and ask is not None
        if returned_symbol != symbol or bid <= 0 or ask <= 0 or bid > ask:
            raise TradernetAdapterError("validation", "quote is contradictory or for another symbol")
        return Quote(
            symbol=returned_symbol,
            bid=bid,
            ask=ask,
            last=last,
            as_of=as_of,
            currency=_text(data, "currency", "curr", required=False),
        )

    def get_market_status(self, symbol: str | None = None) -> Any:
        kwargs = {"ticker": symbol} if symbol else {}
        response = self._read(("get_market_status", "market_status", "get_market"), **kwargs)
        data = _mapping(response, "market-status response")
        if "market" in data:
            data = _mapping(data["market"], "market")
        state = _text(data, "state", "status", "session")
        is_open = _first(data, "is_open", "open", required=False)
        if not isinstance(is_open, bool):
            raise TradernetAdapterError("validation", "market is_open must be boolean")
        normalized = state.lower().replace("_", "-") if state else ""
        try:
            status = MarketSession(normalized)
        except ValueError as exc:
            raise TradernetAdapterError("validation", f"unknown market status: {state}") from exc
        if (status in {MarketSession.OPEN, MarketSession.TRADING}) != is_open:
            raise TradernetAdapterError("validation", "market state contradicts is_open")
        return MarketState(
            symbol=_text(data, "symbol", "ticker", "instr", required=False) or symbol,
            status=status,
            is_open=is_open,
            as_of=_timestamp(data, "as_of", "timestamp", "date"),
        )

    def get_instrument_metadata(self, symbol: str) -> Mapping[str, Any]:
        """Read sizing and risk evidence without supplying exchange defaults."""
        response = self._read(
            (
                "get_instrument_metadata",
                "get_instrument_info",
                "get_security_info",
                "security_info",
            ),
            ticker=symbol,
        )
        data = _mapping(response, "instrument metadata response")
        if "instrument" in data:
            data = _mapping(data["instrument"], "instrument metadata")
        returned_symbol = _text(data, "symbol")
        if returned_symbol != symbol:
            raise TradernetAdapterError(
                "validation", "instrument metadata is for another symbol"
            )
        values = {
            "lot_size": _decimal(data, "lot_size"),
            "tick_size": _decimal(data, "tick_size"),
            "average_daily_notional": _decimal(data, "average_daily_notional"),
            "instrument_type": _text(data, "instrument_type"),
            "tradable": _first(data, "tradable"),
        }
        if any(values[name] is None or values[name] <= 0 for name in (
            "lot_size",
            "tick_size",
            "average_daily_notional",
        )):
            raise TradernetAdapterError(
                "validation", "instrument sizing and liquidity metadata must be positive"
            )
        if not isinstance(values["tradable"], bool):
            raise TradernetAdapterError(
                "validation", "instrument metadata tradable must be boolean"
            )
        return values

    def get_active_orders(self) -> tuple[Any, ...]:
        response = self._read(
            ("get_active_orders", "get_orders", "get_placed"), active_only=True
        )
        orders = tuple(self._parse_order(item) for item in _items(response, "orders", "active_orders"))
        if any(order.status not in _ACTIVE_ORDER_STATUSES for order in orders):
            raise TradernetAdapterError("validation", "active orders contain a terminal status")
        return orders

    def get_order_history(self) -> tuple[Any, ...]:
        response = self._read(
            ("get_order_history", "get_orders", "get_historical"), active_only=False
        )
        return tuple(self._parse_order(item) for item in _items(response, "orders", "history"))

    def reconcile_order(self, intent_id: str) -> Order:
        for reader in (self.get_active_orders, self.get_order_history):
            matches = [order for order in reader() if order.client_order_id == intent_id]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                break
        raise TradernetAdapterError(
            "unknown-outcome",
            f"order intent {intent_id!r} cannot be identified uniquely",
            unknown_outcome=True,
        )

    def _parse_order(self, data: Mapping[str, Any]) -> Any:
        side, quantity = _side_and_quantity(data)
        filled = _decimal(data, "filled_quantity", "filled", "filled_count")
        assert filled is not None
        filled = abs(filled) if side == "buy" else -abs(filled)
        status_value = _text(data, "status", "order_status")
        status = _ORDER_STATUS.get(status_value.lower() if status_value else "")
        if status is None:
            raise TradernetAdapterError("validation", f"unknown order status: {status_value}")
        total = abs(quantity)
        if abs(filled) > total:
            raise TradernetAdapterError("validation", "filled quantity exceeds order quantity")
        if status == "filled" and abs(filled) != total:
            raise TradernetAdapterError("validation", "filled order has incomplete quantity")
        if status == "partially-filled" and not (Decimal(0) < abs(filled) < total):
            raise TradernetAdapterError("validation", "partial status contradicts fill quantity")
        if status in {"pending", "active"} and filled != 0:
            raise TradernetAdapterError("validation", "unfilled status contradicts fill quantity")
        order_type = _text(data, "order_type", "type")
        normalized_type = order_type.lower().replace("-", "_") if order_type else ""
        try:
            parsed_type = OrderType(normalized_type)
        except ValueError as exc:
            raise TradernetAdapterError(
                "validation", f"unknown order type: {order_type}"
            ) from exc
        limit_price = _decimal(data, "limit_price", "limit", required=False)
        stop_price = _decimal(data, "stop_price", "stop", required=False)
        trailing_percent = _decimal(
            data, "trailing_percent", "trailing_percentage", "trailing", required=False
        )
        if normalized_type == "limit" and (limit_price is None or limit_price <= 0):
            raise TradernetAdapterError("validation", "limit order lacks a valid limit price")
        if parsed_type in {OrderType.STOP, OrderType.TRAILING_STOP} and (
            stop_price is None or stop_price <= 0
        ):
            raise TradernetAdapterError("validation", "stop order lacks a valid stop price")
        if parsed_type is OrderType.TRAILING_STOP and (
            trailing_percent is None or trailing_percent <= 0
        ):
            raise TradernetAdapterError("validation", "trailing stop lacks a valid percentage")
        return Order(
            order_id=_text(data, "order_id", "id", "orderId"),
            client_order_id=_text(
                data, "client_order_id", "user_order_id", "clientOrderId", required=False
            ),
            symbol=_text(data, "symbol", "ticker", "instr"),
            side=side,
            quantity=quantity,
            filled_quantity=filled,
            order_type=parsed_type,
            status=status,
            limit_price=limit_price,
            stop_price=stop_price,
            trailing_percent=trailing_percent,
            duration=_text(data, "duration", "order_exp", "expiration"),
            created_at=_timestamp(data, "created_at", "created", "date"),
            updated_at=_timestamp(data, "updated_at", "updated", "date"),
        )

    def get_fills(self, *, order_id: str | None = None) -> tuple[Any, ...]:
        kwargs = {"order_id": order_id} if order_id else {}
        response = self._read(
            ("get_fills", "get_trades", "get_executions", "get_trades_history"), **kwargs
        )
        fills = tuple(self._parse_fill(item) for item in _items(response, "fills", "trades"))
        if order_id and any(fill.order_id != order_id for fill in fills):
            raise TradernetAdapterError("validation", "fill response contains another order")
        return fills

    def _parse_fill(self, data: Mapping[str, Any]) -> Any:
        side, quantity = _side_and_quantity(data)
        price = _decimal(data, "price", "fill_price")
        commission = _decimal(data, "commission", "fee")
        assert price is not None and commission is not None
        if price <= 0 or commission < 0:
            raise TradernetAdapterError("validation", "fill has invalid price or commission")
        return Fill(
            fill_id=_text(data, "fill_id", "trade_id", "id"),
            order_id=_text(data, "order_id", "orderId"),
            symbol=_text(data, "symbol", "ticker", "instr"),
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            currency=_text(data, "currency", "curr"),
            executed_at=_timestamp(data, "executed_at", "timestamp", "date"),
        )

    def get_candles(
        self,
        symbol: str,
        *,
        interval: str,
        lookback: timedelta,
        now: datetime | None = None,
    ) -> Any:
        end = now or self._clock()
        if end.tzinfo is None or lookback <= timedelta(0):
            raise ValueError("candle end must be timezone-aware and lookback must be positive")
        end = end.astimezone(timezone.utc)
        start = end - lookback
        return self._read(
            ("get_candles", "get_historical_data", "candles"),
            ticker=symbol,
            interval=interval,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

    def place_order(
        self,
        *,
        symbol: str,
        signed_quantity: Decimal,
        order_type: str,
        duration: str,
        margin: bool,
        client_order_id: str,
        limit_price: Decimal | None = None,
        reduce_only: bool = False,
        position_reader: Callable[[str], Decimal] | None = None,
    ) -> Any:
        quantity = Decimal(signed_quantity)
        if not symbol or quantity == 0 or not client_order_id or not duration:
            raise TradernetAdapterError("validation", "order intent is incomplete")
        if order_type not in {"market", "limit"}:
            raise TradernetAdapterError("validation", "order type must be market or limit")
        if not isinstance(margin, bool):
            raise TradernetAdapterError("validation", "margin policy must be explicit")
        if order_type == "limit" and (limit_price is None or limit_price <= 0):
            raise TradernetAdapterError("validation", "limit order requires a positive price")
        if order_type == "market" and limit_price is not None:
            raise TradernetAdapterError("validation", "market order cannot include a limit price")
        if reduce_only:
            if position_reader is None:
                raise TradernetAdapterError(
                    "validation", "reduce-only order requires current position reconciliation"
                )
            current = Decimal(position_reader(symbol))
            if current == 0 or current * quantity >= 0 or abs(quantity) > abs(current):
                raise TradernetAdapterError(
                    "rejection", "reduce-only order would not reduce the current position safely"
                )
        kwargs: dict[str, Any] = {
            "ticker": symbol,
            "side": "buy" if quantity > 0 else "sell",
            "count": abs(quantity),
            "order_exp": duration,
            "margin": margin,
            "client_order_id": client_order_id,
            "market_order": order_type == "market",
        }
        if limit_price is not None:
            kwargs["limit_price"] = limit_price
        response = self._mutate(("send_order", "place_order", "create_order"), **kwargs)
        data = _mapping(response, "order response")
        broker_order_id = _text(data, "order_id", "id", "orderId", required=False)
        if broker_order_id is None:
            raise TradernetAdapterError(
                "unknown-outcome",
                "Tradernet accepted an order request without a broker order identity",
                unknown_outcome=True,
            )
        candidates = self.get_active_orders()
        matches = [
            order
            for order in candidates
            if order.order_id == broker_order_id
            or order.client_order_id == client_order_id
        ]
        if not matches:
            matches = [
                order
                for order in self.get_order_history()
                if order.order_id == broker_order_id
                or order.client_order_id == client_order_id
            ]
        if len(matches) != 1:
            raise TradernetAdapterError(
                "unknown-outcome",
                "submitted order cannot be identified uniquely during reconciliation",
                unknown_outcome=True,
            )
        order = matches[0]
        if (
            order.symbol != symbol
            or order.side is not (OrderSide.BUY if quantity > 0 else OrderSide.SELL)
            or order.quantity != quantity
            or order.order_type.value != order_type
            or order.limit_price != limit_price
        ):
            raise TradernetAdapterError(
                "unknown-outcome",
                "broker-reported order contradicts submitted intent",
                unknown_outcome=True,
            )
        return order

    def cancel_order(self, order_id: str, *, client_order_id: str) -> Any:
        if not order_id or not client_order_id:
            raise TradernetAdapterError(
                "validation", "specific broker and caller order identities are required"
            )
        return self._mutate(
            ("delete_order", "cancel_order", "cancel"),
            order_id=order_id,
            client_order_id=client_order_id,
        )

    def set_static_stop(
        self,
        *,
        symbol: str,
        signed_position_quantity: Decimal,
        stop_price: Decimal,
        transition_id: str,
    ) -> Any:
        quantity = Decimal(signed_position_quantity)
        if quantity == 0 or stop_price <= 0 or not transition_id:
            raise TradernetAdapterError("validation", "static-stop intent is incomplete")
        self._mutate(
            ("set_static_stop", "set_stop_order", "stop"),
            ticker=symbol,
            side="sell" if quantity > 0 else "buy",
            count=abs(quantity),
            stop_loss=stop_price,
            client_order_id=transition_id,
        )
        return self._reconcile_stop(
            symbol=symbol,
            position_quantity=quantity,
            stop_price=stop_price,
            trailing_percent=None,
        )

    def set_trailing_stop(
        self,
        *,
        symbol: str,
        signed_position_quantity: Decimal,
        stop_price: Decimal,
        trailing_percent: Decimal,
        transition_id: str,
    ) -> Any:
        quantity = Decimal(signed_position_quantity)
        if quantity == 0 or stop_price <= 0 or trailing_percent <= 0 or not transition_id:
            raise TradernetAdapterError("validation", "trailing-stop intent is incomplete")
        self._mutate(
            ("set_trailing_stop", "set_stop_order", "trailing_stop"),
            ticker=symbol,
            side="sell" if quantity > 0 else "buy",
            count=abs(quantity),
            stop_init_price=stop_price,
            trailing_percent=trailing_percent,
            client_order_id=transition_id,
        )
        return self._reconcile_stop(
            symbol=symbol,
            position_quantity=quantity,
            stop_price=stop_price,
            trailing_percent=trailing_percent,
        )

    def _reconcile_stop(
        self,
        *,
        symbol: str,
        position_quantity: Decimal,
        stop_price: Decimal,
        trailing_percent: Decimal | None,
    ) -> Any:
        expected_side = OrderSide.SELL if position_quantity > 0 else OrderSide.BUY
        response = self._read(("get_active_orders", "get_orders"), active_only=True)
        matches = [
            item
            for item in _items(response, "orders", "active_orders")
            if _text(item, "symbol", "ticker", "instr") == symbol
            and (_text(item, "order_type", "type") or "").lower().replace("_", "-")
            in {"stop", "trailing-stop"}
        ]
        if len(matches) != 1:
            raise TradernetAdapterError(
                "unknown-outcome",
                "protective stop cannot be identified uniquely",
                unknown_outcome=True,
            )
        raw_order = matches[0]
        order = self._parse_order(raw_order)
        confirmed_stop = _decimal(raw_order, "stop_price", "stop")
        confirmed_trailing = _decimal(
            raw_order,
            "trailing_percent",
            "trailing_percentage",
            "trailing",
            required=False,
        )
        if (
            order.side != expected_side
            or abs(order.quantity) != abs(position_quantity)
            or order.status not in _ACTIVE_ORDER_STATUSES
            or confirmed_stop != stop_price
            or confirmed_trailing != trailing_percent
        ):
            raise TradernetAdapterError(
                "unknown-outcome",
                "broker-reported stop contradicts requested protection",
                unknown_outcome=True,
            )
        return StopState(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            status=order.status,
            stop_price=confirmed_stop,
            trailing_percent=confirmed_trailing,
        )

    def get_active_stops(self) -> tuple[StopState, ...]:
        return tuple(
            StopState(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                status=order.status,
                stop_price=order.stop_price,
                trailing_percent=order.trailing_percent,
            )
            for order in self.get_active_orders()
            if order.order_type in {OrderType.STOP, OrderType.TRAILING_STOP}
        )

    def reconcile_stop(self, symbol: str, transition_id: str | None = None) -> StopState:
        matches = [
            order
            for order in self.get_active_orders()
            if order.symbol == symbol
            and order.order_type in {OrderType.STOP, OrderType.TRAILING_STOP}
            and (transition_id is None or order.client_order_id == transition_id)
        ]
        if len(matches) != 1:
            raise TradernetAdapterError(
                "unknown-outcome",
                "protective stop cannot be identified uniquely",
                unknown_outcome=True,
            )
        order = matches[0]
        return StopState(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            status=order.status,
            stop_price=order.stop_price,
            trailing_percent=order.trailing_percent,
        )

    def subscribe(
        self,
        callbacks: Mapping[str, Callable[[Any], None]],
        *,
        reconcile: Callable[[], Any],
        max_reconnects: int = 3,
        reconnect_backoff: float = 0.5,
        on_reconnect: Callable[[int], None] | None = None,
    ) -> None:
        required = {"quote", "portfolio", "order", "market_status"}
        if set(callbacks) != required:
            raise ValueError(f"subscription callbacks must be exactly: {sorted(required)}")
        if max_reconnects < 0 or reconnect_backoff < 0:
            raise ValueError("reconnect policy must be bounded and non-negative")
        method = self._method(("subscribe", "stream", "run_stream"))

        def dispatch(event: Any) -> None:
            data = _mapping(event, "stream event")
            topic = _text(data, "type", "topic", "event")
            if topic not in callbacks:
                raise TradernetAdapterError("validation", f"unknown stream event: {topic}")
            callbacks[topic](self.sanitize(data.get("data", data)))

        for attempt in range(max_reconnects + 1):
            if attempt:
                self._sleep(reconnect_backoff * (2 ** (attempt - 1)))
                if on_reconnect:
                    on_reconnect(attempt)
            reconcile()
            try:
                method(topics=tuple(sorted(required)), callback=dispatch)
                return
            except (TimeoutError, ConnectionError, OSError) as exc:
                if attempt == max_reconnects:
                    message = str(self.sanitize(str(exc))) or type(exc).__name__
                    raise TradernetAdapterError(
                        "transport", f"Tradernet stream reconnect limit reached: {message}"
                    ) from exc


__all__ = [
    "PortfolioSnapshot",
    "TradernetAdapter",
    "TradernetAdapterError",
    "TradernetCredentials",
    "TradernetDependencyError",
    "redact_diagnostics",
]
