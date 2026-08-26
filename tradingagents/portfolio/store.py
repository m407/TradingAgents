"""Transactional SQLite storage for portfolio automation.

This database is deliberately separate from LangGraph checkpoints. Broker payloads
are sanitized before they cross the persistence boundary, while SQLite triggers
protect audit columns from later mutation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
REDACTED = "[REDACTED]"
DEFAULT_LEASE_SECONDS = 60.0

_SECRET_PARTS = (
    "authorization",
    "credential",
    "password",
    "private_key",
    "privatekey",
    "secret",
    "signature",
    "token",
)

RedactionHook = Callable[[tuple[str, ...], Any], Any]


class LeaseLostError(RuntimeError):
    """Raised when an owner tries to mutate a lease it no longer owns."""


def _is_secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in {"api_key", "public_key"} or any(
        part in normalized for part in _SECRET_PARTS
    )


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_plain(item) for item in value]
        return sorted(items, key=lambda item: _canonical_json(item))
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    raise TypeError(f"cannot serialize {type(value).__name__} as an audit payload")


def recursive_redact(
    value: Any,
    hook: RedactionHook | None = None,
    *,
    _path: tuple[str, ...] = (),
) -> Any:
    """Recursively redact known secret keys and invoke an optional leaf hook.

    ``hook`` receives the full key/index path and the already-normalized leaf.
    Returning ``REDACTED`` is useful for configured sensitive identifiers.
    """
    value = _plain(value)
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            path = (*_path, key)
            redacted[key] = (
                REDACTED if _is_secret_key(key) else recursive_redact(item, hook, _path=path)
            )
        return redacted
    if isinstance(value, list):
        return [recursive_redact(item, hook, _path=(*_path, str(i))) for i, item in enumerate(value)]
    return hook(_path, value) if hook is not None else value


def _canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_sha256(namespace: str, *parts: Any) -> str:
    """Hash typed, canonically encoded identity components without truncation."""
    material = _canonical_json({"namespace": namespace, "parts": parts}).encode("ascii")
    return hashlib.sha256(material).hexdigest()


def configuration_fingerprint(
    configuration: Any, *, exclude: Sequence[str] = (), hook: RedactionHook | None = None
) -> str:
    """Fingerprint effective non-secret configuration.

    Secret-bearing fields and explicitly excluded top-level fields are omitted,
    rather than replaced, so credential presence cannot change cycle identity.
    """
    plain = _plain(configuration)
    if not isinstance(plain, Mapping):
        raise TypeError("configuration must be mapping-like")
    excluded = {name.casefold() for name in exclude}

    def strip(value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                if _is_secret_key(key) or (not path and key.casefold() in excluded):
                    continue
                result[key] = strip(item, (*path, key))
            return result
        if isinstance(value, list):
            return [strip(item, (*path, str(index))) for index, item in enumerate(value)]
        return hook(path, value) if hook is not None else value

    return stable_sha256("configuration", strip(plain))


def cycle_identity(account_scope: str, fingerprint: str, trading_date: date | str) -> str:
    return stable_sha256("cycle", account_scope, fingerprint, str(trading_date))


def position_incarnation_identity(
    account_scope: str, symbol: str, side: str | int, opening_identity: str
) -> str:
    return stable_sha256(
        "position-incarnation", account_scope, symbol.upper(), str(side), opening_identity
    )


def order_intent_identity(cycle_id: str, symbol: str, action: str, sequence: int = 0) -> str:
    return stable_sha256("order-intent", cycle_id, symbol.upper(), action, sequence)


def stop_transition_identity(
    incarnation_id: str, transition: str, requested_state: Any, sequence: int = 0
) -> str:
    return stable_sha256(
        "stop-transition", incarnation_id, transition, requested_state, sequence
    )


# Alternate names make the identity helpers read naturally at call sites.
stable_cycle_identity = cycle_identity
stable_position_incarnation_identity = position_incarnation_identity
stable_order_intent_identity = order_intent_identity
stable_stop_transition_identity = stop_transition_identity


_SCHEMA = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE configurations (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    account_scope TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE cycle_runs (
    id INTEGER PRIMARY KEY,
    cycle_identity TEXT NOT NULL UNIQUE,
    configuration_id INTEGER NOT NULL REFERENCES configurations(id),
    account_scope TEXT NOT NULL,
    trading_date TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    cycle_run_id INTEGER REFERENCES cycle_runs(id),
    account_scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE intents (
    id INTEGER PRIMARY KEY,
    intent_identity TEXT NOT NULL UNIQUE,
    cycle_run_id INTEGER NOT NULL REFERENCES cycle_runs(id),
    account_scope TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    requested_json TEXT NOT NULL,
    status TEXT NOT NULL,
    broker_order_id TEXT,
    reconciliation_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE broker_operations (
    id INTEGER PRIMARY KEY,
    operation_identity TEXT NOT NULL UNIQUE,
    intent_id INTEGER REFERENCES intents(id),
    transition_id INTEGER REFERENCES protection_transitions(id) DEFERRABLE INITIALLY DEFERRED,
    operation_kind TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT,
    broker_order_id TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE fills (
    id INTEGER PRIMARY KEY,
    fill_identity TEXT NOT NULL UNIQUE,
    broker_fill_id TEXT NOT NULL UNIQUE,
    broker_order_id TEXT NOT NULL,
    operation_id INTEGER REFERENCES broker_operations(id),
    account_scope TEXT NOT NULL,
    symbol TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    filled_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE protection_states (
    id INTEGER PRIMARY KEY,
    incarnation_identity TEXT NOT NULL UNIQUE,
    account_scope TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side INTEGER NOT NULL CHECK (side IN (-1, 1)),
    state TEXT NOT NULL,
    confirmed_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE protection_transitions (
    id INTEGER PRIMARY KEY,
    transition_identity TEXT NOT NULL UNIQUE,
    protection_state_id INTEGER NOT NULL REFERENCES protection_states(id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    requested_json TEXT NOT NULL,
    confirmed_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE leases (
    account_scope TEXT NOT NULL,
    lease_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    heartbeat_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (account_scope, lease_kind)
);

CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    account_scope TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX snapshots_cycle_idx ON snapshots(cycle_run_id, id);
CREATE INDEX intents_cycle_idx ON intents(cycle_run_id, id);
CREATE INDEX operations_intent_idx ON broker_operations(intent_id, id);
CREATE INDEX fills_order_idx ON fills(broker_order_id, id);
CREATE INDEX protection_account_symbol_idx ON protection_states(account_scope, symbol);
CREATE INDEX alerts_account_idx ON alerts(account_scope, id);

CREATE TRIGGER configurations_immutable_update BEFORE UPDATE ON configurations
BEGIN SELECT RAISE(ABORT, 'configuration audit rows are immutable'); END;
CREATE TRIGGER configurations_immutable_delete BEFORE DELETE ON configurations
BEGIN SELECT RAISE(ABORT, 'configuration audit rows are immutable'); END;
CREATE TRIGGER snapshots_immutable_update BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshot audit rows are immutable'); END;
CREATE TRIGGER snapshots_immutable_delete BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshot audit rows are immutable'); END;
CREATE TRIGGER fills_immutable_update BEFORE UPDATE ON fills
BEGIN SELECT RAISE(ABORT, 'fill audit rows are immutable'); END;
CREATE TRIGGER fills_immutable_delete BEFORE DELETE ON fills
BEGIN SELECT RAISE(ABORT, 'fill audit rows are immutable'); END;
CREATE TRIGGER alerts_immutable_update BEFORE UPDATE ON alerts
BEGIN SELECT RAISE(ABORT, 'alert audit rows are immutable'); END;
CREATE TRIGGER alerts_immutable_delete BEFORE DELETE ON alerts
BEGIN SELECT RAISE(ABORT, 'alert audit rows are immutable'); END;

CREATE TRIGGER cycle_audit_immutable BEFORE UPDATE ON cycle_runs
WHEN NEW.cycle_identity != OLD.cycle_identity
  OR NEW.configuration_id != OLD.configuration_id
  OR NEW.account_scope != OLD.account_scope
  OR NEW.trading_date != OLD.trading_date
  OR NEW.input_json != OLD.input_json
  OR (OLD.result_json IS NOT NULL AND NEW.result_json IS NOT OLD.result_json)
BEGIN SELECT RAISE(ABORT, 'cycle audit columns are immutable'); END;

CREATE TRIGGER intent_audit_immutable BEFORE UPDATE ON intents
WHEN NEW.intent_identity != OLD.intent_identity
  OR NEW.cycle_run_id != OLD.cycle_run_id
  OR NEW.account_scope != OLD.account_scope
  OR NEW.symbol != OLD.symbol
  OR NEW.kind != OLD.kind
  OR NEW.requested_json != OLD.requested_json
  OR (OLD.reconciliation_json IS NOT NULL
      AND NEW.reconciliation_json IS NOT OLD.reconciliation_json)
BEGIN SELECT RAISE(ABORT, 'intent audit columns are immutable'); END;

CREATE TRIGGER operation_audit_immutable BEFORE UPDATE ON broker_operations
WHEN NEW.operation_identity != OLD.operation_identity
  OR NEW.intent_id IS NOT OLD.intent_id
  OR NEW.transition_id IS NOT OLD.transition_id
  OR NEW.operation_kind != OLD.operation_kind
  OR NEW.request_json != OLD.request_json
  OR (OLD.response_json IS NOT NULL AND NEW.response_json IS NOT OLD.response_json)
BEGIN SELECT RAISE(ABORT, 'broker operation audit columns are immutable'); END;

CREATE TRIGGER transition_audit_immutable BEFORE UPDATE ON protection_transitions
WHEN NEW.transition_identity != OLD.transition_identity
  OR NEW.protection_state_id != OLD.protection_state_id
  OR NEW.from_state != OLD.from_state
  OR NEW.to_state != OLD.to_state
  OR NEW.requested_json != OLD.requested_json
  OR (OLD.confirmed_json IS NOT NULL AND NEW.confirmed_json IS NOT OLD.confirmed_json)
BEGIN SELECT RAISE(ABORT, 'protection transition audit columns are immutable'); END;
"""


def _utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


class PortfolioStore:
    """Operational repository with one short SQLite transaction per mutation."""

    def __init__(
        self,
        path: str | Path,
        *,
        account_scope: str | None = None,
        redaction_hook: RedactionHook | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        timeout: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.account_scope = account_scope
        self.redaction_hook = redaction_hook
        self.lease_seconds = lease_seconds
        self.timeout = timeout
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=self.timeout, isolation_level=None, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version == 0:
                applied_at = _utc_now().replace("'", "''")
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _SCHEMA
                    + f"\nINSERT INTO schema_migrations VALUES (1, '{applied_at}');\n"
                    + f"PRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
                )

    @property
    def schema_version(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Generator[sqlite3.Connection, None, None]:
        """Commit all writes together or roll all of them back on interruption."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _payload(self, value: Any) -> str:
        return _canonical_json(recursive_redact(value, self.redaction_hook))

    @staticmethod
    def _write_connection(
        supplied: sqlite3.Connection | None, manager: Any
    ) -> tuple[sqlite3.Connection, Any | None]:
        if supplied is not None:
            return supplied, None
        context = manager()
        return context.__enter__(), context

    @staticmethod
    def _close_write(context: Any | None, error: BaseException | None = None) -> None:
        if context is not None:
            context.__exit__(type(error) if error else None, error, error.__traceback__ if error else None)

    def record_configuration(
        self,
        account_scope: str,
        configuration: Any,
        *,
        fingerprint: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        fingerprint = fingerprint or configuration_fingerprint(configuration)
        now = _utc_now()
        conn, context = self._write_connection(connection, self.transaction)
        error = None
        try:
            conn.execute(
                "INSERT OR IGNORE INTO configurations"
                " (fingerprint, account_scope, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (fingerprint, account_scope, self._payload(configuration), now),
            )
            row = conn.execute(
                "SELECT id, account_scope FROM configurations WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if row["account_scope"] != account_scope:
                raise ValueError("configuration fingerprint already belongs to another account scope")
            return int(row["id"])
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._close_write(context, error)

    def start_cycle_record(
        self,
        cycle_id: str,
        account_scope: str,
        trading_date: date | str,
        fingerprint: str,
        immutable_inputs: Any,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        conn, context = self._write_connection(connection, self.transaction)
        error = None
        try:
            configuration_id = self.record_configuration(
                account_scope,
                {"fingerprint": fingerprint},
                fingerprint=fingerprint,
                connection=conn,
            )
            now = _utc_now()
            conn.execute(
                "INSERT OR IGNORE INTO cycle_runs"
                " (cycle_identity, configuration_id, account_scope, trading_date, status,"
                " input_json, created_at, updated_at) VALUES (?, ?, ?, ?, 'running', ?, ?, ?)",
                (
                    cycle_id,
                    configuration_id,
                    account_scope,
                    str(trading_date),
                    self._payload(immutable_inputs),
                    now,
                    now,
                ),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM cycle_runs WHERE cycle_identity = ?", (cycle_id,)
                ).fetchone()
            )
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._close_write(context, error)

    def start_cycle(self, cycle_id: str, immutable_inputs: Mapping[str, Any]) -> None:
        account = str(immutable_inputs.get("account_scope") or self._required_account())
        fingerprint = str(immutable_inputs.get("configuration_fingerprint") or "unspecified")
        trading_date = str(immutable_inputs.get("trading_date") or "unspecified")
        self.start_cycle_record(cycle_id, account, trading_date, fingerprint, immutable_inputs)

    def get_cycle_row(self, cycle_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM cycle_runs WHERE cycle_identity = ?", (cycle_id,)
                ).fetchone()
            )

    def get_cycle(self, cycle_id: str) -> Any | None:
        row = self.get_cycle_row(cycle_id)
        if row is None:
            return None
        payload = json.loads(row["result_json"]) if row["result_json"] else None
        if payload is None:
            from types import SimpleNamespace

            return SimpleNamespace(cycle_id=cycle_id, status=row["status"])
        try:
            from tradingagents.portfolio.cycle import CyclePlan, CycleResult, SymbolResearch

            research = tuple(SymbolResearch(**item) for item in payload.get("research", ()))
            plan_payload = payload.get("plan")
            plan = CyclePlan(**plan_payload) if isinstance(plan_payload, dict) else plan_payload
            return CycleResult(**{**payload, "research": research, "plan": plan})
        except (ImportError, TypeError):
            from types import SimpleNamespace

            return SimpleNamespace(**payload)

    def update_cycle_state(self, cycle_id: str, status: str, result: Any | None = None) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE cycle_runs SET status = ?, result_json = COALESCE(result_json, ?),"
                " updated_at = ? WHERE cycle_identity = ?",
                (status, None if result is None else self._payload(result), _utc_now(), cycle_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown cycle {cycle_id}")

    def finish_cycle(self, cycle_id: str, result: Any) -> None:
        status = str(getattr(result, "status", "completed"))
        self.update_cycle_state(cycle_id, status, result)

    def record_snapshot(
        self,
        account_scope: str,
        kind: str,
        payload: Any,
        *,
        cycle_id: str | None = None,
        observed_at: datetime | str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        encoded = self._payload(payload)
        digest = hashlib.sha256(encoded.encode("ascii")).hexdigest()
        conn, context = self._write_connection(connection, self.transaction)
        error = None
        try:
            cycle_row = (
                conn.execute(
                    "SELECT id FROM cycle_runs WHERE cycle_identity = ?", (cycle_id,)
                ).fetchone()
                if cycle_id
                else None
            )
            cursor = conn.execute(
                "INSERT INTO snapshots"
                " (cycle_run_id, account_scope, kind, observed_at, payload_json, payload_sha256,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cycle_row["id"] if cycle_row else None,
                    account_scope,
                    kind,
                    str(observed_at or _utc_now()),
                    encoded,
                    digest,
                    _utc_now(),
                ),
            )
            return int(cursor.lastrowid)
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._close_write(context, error)

    def append_cycle_event(self, cycle_id: str, kind: str, payload: Any) -> None:
        row = self.get_cycle_row(cycle_id)
        if row is None:
            raise KeyError(f"unknown cycle {cycle_id}")
        self.record_snapshot(row["account_scope"], kind, payload, cycle_id=cycle_id)

    def record_intent(
        self,
        intent_id: str,
        cycle_id: str,
        account_scope: str,
        symbol: str,
        kind: str,
        requested: Any,
        *,
        status: str = "planned",
    ) -> int:
        now = _utc_now()
        with self.transaction() as connection:
            cycle = connection.execute(
                "SELECT id FROM cycle_runs WHERE cycle_identity = ?", (cycle_id,)
            ).fetchone()
            if cycle is None:
                raise KeyError(f"unknown cycle {cycle_id}")
            connection.execute(
                "INSERT OR IGNORE INTO intents"
                " (intent_identity, cycle_run_id, account_scope, symbol, kind, requested_json,"
                " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    intent_id,
                    cycle["id"],
                    account_scope,
                    symbol.upper(),
                    kind,
                    self._payload(requested),
                    status,
                    now,
                    now,
                ),
            )
            return int(
                connection.execute(
                    "SELECT id FROM intents WHERE intent_identity = ?", (intent_id,)
                ).fetchone()["id"]
            )

    def update_intent_state(
        self,
        intent_id: str,
        status: str,
        *,
        broker_order_id: str | None = None,
        reconciliation: Any | None = None,
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE intents SET status = ?, broker_order_id = COALESCE(broker_order_id, ?),"
                " reconciliation_json = COALESCE(reconciliation_json, ?), updated_at = ?"
                " WHERE intent_identity = ?",
                (
                    status,
                    broker_order_id,
                    None if reconciliation is None else self._payload(reconciliation),
                    _utc_now(),
                    intent_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown intent {intent_id}")

    def list_intents(self, account_scope: str, *, cycle_id: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT i.* FROM intents i JOIN cycle_runs c ON c.id = i.cycle_run_id"
            " WHERE i.account_scope = ?"
        )
        parameters: list[Any] = [account_scope]
        if cycle_id is not None:
            sql += " AND c.cycle_identity = ?"
            parameters.append(cycle_id)
        sql += " ORDER BY i.id"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters)]

    def record_broker_operation(
        self,
        operation_id: str,
        operation_kind: str,
        request: Any,
        *,
        intent_id: str | None = None,
        transition_id: str | None = None,
        state: str = "pending",
    ) -> int:
        now = _utc_now()
        with self.transaction() as connection:
            intent = (
                connection.execute(
                    "SELECT id FROM intents WHERE intent_identity = ?", (intent_id,)
                ).fetchone()
                if intent_id
                else None
            )
            transition = (
                connection.execute(
                    "SELECT id FROM protection_transitions WHERE transition_identity = ?",
                    (transition_id,),
                ).fetchone()
                if transition_id
                else None
            )
            connection.execute(
                "INSERT OR IGNORE INTO broker_operations"
                " (operation_identity, intent_id, transition_id, operation_kind, request_json,"
                " state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    intent["id"] if intent else None,
                    transition["id"] if transition else None,
                    operation_kind,
                    self._payload(request),
                    state,
                    now,
                    now,
                ),
            )
            return int(
                connection.execute(
                    "SELECT id FROM broker_operations WHERE operation_identity = ?",
                    (operation_id,),
                ).fetchone()["id"]
            )

    def update_broker_operation(
        self,
        operation_id: str,
        state: str,
        *,
        response: Any | None = None,
        broker_order_id: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE broker_operations SET state = ?, response_json = COALESCE(response_json, ?),"
                " broker_order_id = COALESCE(broker_order_id, ?), updated_at = ?"
                " WHERE operation_identity = ?",
                (
                    state,
                    None if response is None else self._payload(response),
                    broker_order_id,
                    _utc_now(),
                    operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown broker operation {operation_id}")

    def get_broker_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM broker_operations WHERE operation_identity = ?", (operation_id,)
                ).fetchone()
            )

    def record_fill(
        self,
        broker_fill_id: str,
        broker_order_id: str,
        account_scope: str,
        symbol: str,
        payload: Any,
        filled_at: datetime | str,
        *,
        operation_id: str | None = None,
        fill_id: str | None = None,
    ) -> int:
        fill_id = fill_id or stable_sha256("fill", account_scope, broker_fill_id)
        with self.transaction() as connection:
            operation = (
                connection.execute(
                    "SELECT id FROM broker_operations WHERE operation_identity = ?",
                    (operation_id,),
                ).fetchone()
                if operation_id
                else None
            )
            connection.execute(
                "INSERT OR IGNORE INTO fills"
                " (fill_identity, broker_fill_id, broker_order_id, operation_id, account_scope,"
                " symbol, payload_json, filled_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fill_id,
                    broker_fill_id,
                    broker_order_id,
                    operation["id"] if operation else None,
                    account_scope,
                    symbol.upper(),
                    self._payload(payload),
                    str(filled_at),
                    _utc_now(),
                ),
            )
            return int(
                connection.execute(
                    "SELECT id FROM fills WHERE broker_fill_id = ?", (broker_fill_id,)
                ).fetchone()["id"]
            )

    def save_protection_state_record(
        self,
        incarnation_id: str,
        account_scope: str,
        symbol: str,
        side: int,
        state: str,
        confirmed: Any,
    ) -> int:
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO protection_states"
                " (incarnation_identity, account_scope, symbol, side, state, confirmed_json,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(incarnation_identity) DO UPDATE SET"
                " state = excluded.state, confirmed_json = excluded.confirmed_json,"
                " updated_at = excluded.updated_at",
                (
                    incarnation_id,
                    account_scope,
                    symbol.upper(),
                    side,
                    state,
                    self._payload(confirmed),
                    now,
                    now,
                ),
            )
            return int(
                connection.execute(
                    "SELECT id FROM protection_states WHERE incarnation_identity = ?",
                    (incarnation_id,),
                ).fetchone()["id"]
            )

    def save_protection_state(self, symbol: str, state: Any) -> None:
        account = str(_value(state, "account_scope", self._required_account()))
        quantity = Decimal(str(_value(state, "quantity", _value(state, "signed_quantity", 1))))
        side = 1 if quantity > 0 else -1
        incarnation = str(
            _value(
                state,
                "incarnation_id",
                _value(
                    state,
                    "incarnation_identity",
                    position_incarnation_identity(
                        account, symbol, side, _value(state, "opened_at", "legacy")
                    ),
                ),
            )
        )
        state_name = str(
            _value(state, "phase", _value(state, "status", _value(state, "state", "unknown")))
        )
        self.save_protection_state_record(incarnation, account, symbol, side, state_name, state)

    def load_protection_states(self, account_scope: str) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT symbol, confirmed_json FROM protection_states"
                " WHERE account_scope = ? ORDER BY id",
                (account_scope,),
            )
            return {row["symbol"]: json.loads(row["confirmed_json"]) for row in rows}

    def record_transition(
        self,
        transition_id: str,
        incarnation_id: str,
        from_state: str,
        to_state: str,
        requested: Any,
        *,
        status: str = "pending",
    ) -> int:
        now = _utc_now()
        with self.transaction() as connection:
            protection = connection.execute(
                "SELECT id FROM protection_states WHERE incarnation_identity = ?",
                (incarnation_id,),
            ).fetchone()
            if protection is None:
                raise KeyError(f"unknown position incarnation {incarnation_id}")
            connection.execute(
                "INSERT OR IGNORE INTO protection_transitions"
                " (transition_identity, protection_state_id, from_state, to_state, requested_json,"
                " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition_id,
                    protection["id"],
                    from_state,
                    to_state,
                    self._payload(requested),
                    status,
                    now,
                    now,
                ),
            )
            return int(
                connection.execute(
                    "SELECT id FROM protection_transitions WHERE transition_identity = ?",
                    (transition_id,),
                ).fetchone()["id"]
            )

    def confirm_transition(self, transition_id: str, confirmed: Any, status: str = "confirmed") -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE protection_transitions SET confirmed_json = COALESCE(confirmed_json, ?),"
                " status = ?, updated_at = ? WHERE transition_identity = ?",
                (self._payload(confirmed), status, _utc_now(), transition_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown protection transition {transition_id}")

    def append_protection_event(self, symbol: str, kind: str, payload: Any) -> None:
        account = str(_value(payload, "account_scope", self._required_account()))
        self.record_snapshot(account, f"protection:{symbol.upper()}:{kind}", payload)

    def record_compatibility_evidence(
        self, operation: str, side: str, payload: Any
    ) -> int:
        if operation not in {"static-stop", "break-even", "trailing", "tighten-trailing"}:
            raise ValueError("unsupported stop compatibility operation")
        if side not in {"long", "short"}:
            raise ValueError("compatibility side must be long or short")
        return self.record_snapshot(
            self._required_account(), f"compatibility:{operation}:{side}", payload
        )

    def has_stop_compatibility_evidence(self) -> bool:
        required = {
            "compatibility:tighten-trailing:long",
            "compatibility:tighten-trailing:short",
        }
        with self.connect() as connection:
            found = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT kind FROM snapshots WHERE account_scope = ?"
                    " AND kind LIKE 'compatibility:%'",
                    (self._required_account(),),
                )
            }
        return required <= found

    def raise_alert(
        self,
        severity: str,
        message: str,
        context: Mapping[str, Any],
        *,
        account_scope: str | None = None,
    ) -> int:
        account = account_scope or str(context.get("account_scope") or self._required_account())
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO alerts (account_scope, severity, message, context_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (account, severity, message, self._payload(context), _utc_now()),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _split_scope(scope: str) -> tuple[str, str]:
        if ":" not in scope:
            raise ValueError("lease scope must be '<kind>:<account_scope>'")
        kind, account = scope.split(":", 1)
        if kind not in {"cycle", "protection", "daily_cycle", "protection_supervisor"}:
            raise ValueError(f"unsupported lease kind {kind!r}")
        return account, kind

    def acquire_account_lease(
        self,
        account_scope: str,
        lease_kind: str,
        owner_id: str,
        *,
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        expires = now + (self.lease_seconds if ttl_seconds is None else ttl_seconds)
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO leases"
                " (account_scope, lease_kind, owner_id, heartbeat_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(account_scope, lease_kind) DO UPDATE SET"
                " owner_id = excluded.owner_id, heartbeat_at = excluded.heartbeat_at,"
                " expires_at = excluded.expires_at"
                " WHERE leases.expires_at <= excluded.heartbeat_at"
                " OR leases.owner_id = excluded.owner_id",
                (account_scope, lease_kind, owner_id, now, expires),
            )
            return cursor.rowcount == 1

    def acquire_lease(
        self,
        scope: str,
        owner: str,
        *,
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> bool:
        account, kind = self._split_scope(scope)
        return self.acquire_account_lease(
            account, kind, owner, ttl_seconds=ttl_seconds, now=now
        )

    def heartbeat_lease(
        self,
        scope: str,
        owner: str,
        *,
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> bool:
        account, kind = self._split_scope(scope)
        now = time.time() if now is None else now
        expires = now + (self.lease_seconds if ttl_seconds is None else ttl_seconds)
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE leases SET heartbeat_at = ?, expires_at = ?"
                " WHERE account_scope = ? AND lease_kind = ? AND owner_id = ?"
                " AND expires_at > ?",
                (now, expires, account, kind, owner, now),
            )
            return cursor.rowcount == 1

    def release_lease(self, scope: str, owner: str) -> None:
        account, kind = self._split_scope(scope)
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM leases WHERE account_scope = ? AND lease_kind = ? AND owner_id = ?",
                (account, kind, owner),
            )

    def get_lease(self, account_scope: str, lease_kind: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM leases WHERE account_scope = ? AND lease_kind = ?",
                    (account_scope, lease_kind),
                ).fetchone()
            )

    def _required_account(self) -> str:
        if not self.account_scope:
            raise ValueError("account_scope is required for this operation")
        return self.account_scope


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


OperationalStore = PortfolioStore
SQLitePortfolioStore = PortfolioStore
