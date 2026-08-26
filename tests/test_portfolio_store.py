from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tradingagents.portfolio.models import Fill, Order, Position
from tradingagents.portfolio.reconciliation import reconcile_authoritative_state
from tradingagents.portfolio.store import (
    REDACTED,
    PortfolioStore,
    configuration_fingerprint,
    cycle_identity,
    order_intent_identity,
    position_incarnation_identity,
    recursive_redact,
    stop_transition_identity,
)

NOW = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


def _start_cycle(store: PortfolioStore, cycle_id: str = "cycle-1") -> None:
    store.start_cycle_record(
        cycle_id,
        "account-1",
        date(2026, 8, 11),
        "config-1",
        {"account_scope": "account-1"},
    )


def _order(**overrides):
    values = {
        "order_id": "broker-1",
        "client_order_id": None,
        "symbol": "AAPL",
        "side": "buy",
        "quantity": "2",
        "filled_quantity": "2",
        "order_type": "market",
        "status": "filled",
        "duration": "day",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Order(**values)


def _fill():
    return Fill(
        fill_id="fill-1",
        order_id="broker-1",
        symbol="AAPL",
        side="buy",
        quantity="2",
        price="100.01",
        commission="0.02",
        currency="USD",
        executed_at=NOW,
    )


def test_schema_migration_enables_wal_foreign_keys_and_all_tables(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.sqlite")
    expected = {
        "configurations",
        "cycle_runs",
        "snapshots",
        "intents",
        "broker_operations",
        "fills",
        "protection_states",
        "protection_transitions",
        "leases",
        "alerts",
    }

    with store.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert expected <= tables
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO intents (intent_identity, cycle_run_id, account_scope, symbol,"
                " kind, requested_json, status, created_at, updated_at)"
                " VALUES ('bad', 999, 'account-1', 'AAPL', 'open', '{}', 'planned', 'x', 'x')"
            )


def test_audit_payloads_are_recursively_redacted_and_immutable(tmp_path):
    store = PortfolioStore(
        tmp_path / "portfolio.sqlite",
        redaction_hook=lambda path, value: REDACTED if value == "account-sensitive" else value,
    )
    configuration_id = store.record_configuration(
        "account-1",
        {
            "public_key": "public-secret",
            "nested": [{"private-key": "private-secret", "password": "password-secret"}],
            "account": "account-sensitive",
            "safe": "retained",
        },
    )

    with store.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM configurations WHERE id = ?", (configuration_id,)
        ).fetchone()
        payload = json.loads(row[0])
        assert payload == {
            "account": REDACTED,
            "nested": [{"password": REDACTED, "private-key": REDACTED}],
            "public_key": REDACTED,
            "safe": "retained",
        }
        assert all(
            secret not in row[0]
            for secret in ("public-secret", "private-secret", "password-secret")
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE configurations SET payload_json = '{}' WHERE id = ?", (configuration_id,)
            )

    assert recursive_redact({"items": [{"signature": "secret"}]}) == {
        "items": [{"signature": REDACTED}]
    }


def test_operation_response_is_write_once_and_redacted(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.sqlite")
    operation_id = "operation-1"
    store.record_broker_operation(operation_id, "place-order", {"token": "request-secret"})
    store.update_broker_operation(operation_id, "confirmed", response={"token": "first-secret"})
    store.update_broker_operation(operation_id, "confirmed", response={"safe": "second"})

    row = store.get_broker_operation(operation_id)
    assert json.loads(row["request_json"]) == {"token": REDACTED}
    assert json.loads(row["response_json"]) == {"token": REDACTED}


def test_interrupted_transaction_rolls_back_every_write(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.sqlite")

    with pytest.raises(RuntimeError, match="interrupt"), store.transaction() as connection:
        connection.execute(
            "INSERT INTO alerts (account_scope, severity, message, context_json, created_at)"
            " VALUES ('account-1', 'ERROR', 'first', '{}', 'now')"
        )
        raise RuntimeError("interrupt")

    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0


def test_lease_has_one_concurrent_owner_then_expires_and_heartbeats(tmp_path):
    path = tmp_path / "portfolio.sqlite"
    stores = (PortfolioStore(path), PortfolioStore(path))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: item[1].acquire_lease(
                    "cycle:account-1", item[0], ttl_seconds=10, now=100
                ),
                zip(("owner-a", "owner-b"), stores, strict=True),
            )
        )

    assert sorted(results) == [False, True]
    winner = ("owner-a", "owner-b")[results.index(True)]
    loser = ("owner-a", "owner-b")[results.index(False)]
    assert stores[0].heartbeat_lease("cycle:account-1", loser, now=101) is False
    assert stores[0].heartbeat_lease("cycle:account-1", winner, ttl_seconds=10, now=101)
    assert stores[1].acquire_lease("cycle:account-1", loser, now=110) is False
    assert stores[1].acquire_lease("cycle:account-1", loser, now=111)
    assert stores[0].heartbeat_lease("cycle:account-1", winner, now=112) is False


def test_stable_identities_and_fingerprint_exclude_secrets():
    first = {
        "risk": {"maximum": Decimal("0.10")},
        "public_key": "public-a",
        "credentials": {"api_key": "api-a", "private_key": "private-a"},
    }
    second = {
        "credentials": {"private_key": "private-b", "api_key": "api-b"},
        "public_key": "public-b",
        "risk": {"maximum": Decimal("0.10")},
    }

    assert configuration_fingerprint(first) == configuration_fingerprint(second)
    assert configuration_fingerprint(first) != configuration_fingerprint(
        {**first, "risk": {"maximum": Decimal("0.11")}}
    )
    assert cycle_identity("account-1", "config-1", "2026-08-11") == cycle_identity(
        "account-1", "config-1", date(2026, 8, 11)
    )
    assert position_incarnation_identity("account-1", "aapl", 1, "open-1") == (
        position_incarnation_identity("account-1", "AAPL", 1, "open-1")
    )
    assert order_intent_identity("cycle-1", "aapl", "open", 0) == order_intent_identity(
        "cycle-1", "AAPL", "open", 0
    )
    assert stop_transition_identity("position-1", "trail", {"gap": "1"}) == (
        stop_transition_identity("position-1", "trail", {"gap": "1"})
    )


def test_unknown_outcome_without_broker_evidence_is_blocked():
    result = reconcile_authoritative_state(
        [{"intent_identity": "intent-1", "symbol": "AAPL", "status": "unknown"}],
        [],
        [],
        [],
        [],
    )

    assert result.blocked
    assert result.resolutions[0].state == "unknown"
    assert {item.code for item in result.discrepancies} == {"unknown_outcome_unresolved"}


def test_restart_uses_persisted_order_identity_and_authoritative_fill(tmp_path):
    path = tmp_path / "portfolio.sqlite"
    store = PortfolioStore(path)
    _start_cycle(store)
    store.record_intent(
        "intent-1", "cycle-1", "account-1", "AAPL", "open-long", {"quantity": "2"},
        status="unknown",
    )
    store.update_intent_state("intent-1", "unknown", broker_order_id="broker-1")

    restarted = PortfolioStore(path)
    result = reconcile_authoritative_state(
        restarted.list_intents("account-1"),
        [
            Position(
                position_id="position-1",
                symbol="AAPL",
                quantity="2",
                average_price="100.01",
                market_value="200.02",
                currency="USD",
                unrealized_pnl="0",
            )
        ],
        [],
        [],
        [_fill()],
    )

    assert result.state == "reconciled"
    assert result.resolutions[0].state == "filled"
    assert result.resolutions[0].broker_order_id == "broker-1"
    assert result.resolutions[0].fill_ids == ("fill-1",)


def test_unexplained_broker_order_blocks_authoritative_reconciliation():
    result = reconcile_authoritative_state([], [], [_order(order_id="unowned")], [], [])

    assert result.blocked
    assert {item.code for item in result.discrepancies} == {"unowned_broker_order"}


def test_live_stop_compatibility_requires_long_and_short_tightening(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.sqlite", account_scope="account-1")
    store.record_compatibility_evidence("tighten-trailing", "long", {"confirmed": True})
    assert not store.has_stop_compatibility_evidence()

    store.record_compatibility_evidence("tighten-trailing", "short", {"confirmed": True})

    assert store.has_stop_compatibility_evidence()
