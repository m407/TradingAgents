from __future__ import annotations

import logging
import socket

import pytest

from tradernet_global import client as module
from tradernet_global.models import FreedomPortfolioError


def snapshot(rows=None):
    return {"result": {"ps": {"loaded": True, "pos": rows or [], "acc": [{"curr": "USD", "s": "2"}]}}}


class FakeClient:
    def __init__(self, data=None, quote_errors=()):
        self.data = data or snapshot()
        self.quote_errors = set(quote_errors)
        self.calls = []
        self.closed = False

    def account_summary(self):
        self.calls.append(("snapshot",))
        return self.data

    def get_quotes(self, tickers):
        self.calls.append(("quotes", list(tickers)))
        if tuple(tickers) in self.quote_errors:
            raise RuntimeError("SECRET response")
        return {"result": {"quotes": [{"ticker": ticker, "market": 1, "bbp": 10, "bap": 11} for ticker in tickers]}}


def stock(ticker, kind=1, quantity="1", price="9"):
    return {"ticker": ticker, "t": kind, "q": quantity, "curr": "USD", "mkt_price": price}


def test_snapshot_and_quotes_use_v3_commands_and_unique_bounded_batches(monkeypatch):
    rows = [stock(f"S{i}.US") for i in range(5)] + [{**stock("REPO", 8), "fv": 100, "accruedint_a": 0}, stock("S0.US")]
    fake = FakeClient(snapshot(rows))
    result = module.fetch_freedom_portfolio(client=fake, quote_batch_size=2)
    assert fake.calls == [("snapshot",), ("quotes", ["S0.US", "S1.US"]), ("quotes", ["S2.US", "S3.US"]), ("quotes", ["S4.US"])]
    assert len(result.positions) == 7
    assert result.positions[5].price_source == "portfolio"


def test_no_quotes_for_zero_rp_or_fx_rows():
    fake = FakeClient(snapshot([stock("ZERO", quantity="0"), stock("R", 8),
                                {**stock("FX"), "base_currency": "EUR"}]))
    module.fetch_freedom_portfolio(client=fake)
    assert fake.calls == [("snapshot",)]


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "forbidden"),
                                            (429, "rate_limited"), (503, "api_request_failed"),
                                            (302, "redirect_refused")])
def test_transport_statuses_are_safe(monkeypatch, status, code):
    class Response:
        status_code = status
        def raise_for_status(self):
            if status >= 400:
                import requests
                raise requests.HTTPError("SECRET body")
    class Session:
        def request(self, *args, **kwargs):
            assert kwargs["timeout"] == 3
            assert kwargs["allow_redirects"] is False
            return Response()
    api = module._FreedomTradernet("PUB_SECRET", "PRIV_SECRET", 3)
    api._freedom_session = Session()
    with pytest.raises(FreedomPortfolioError) as error:
        api.request("post", "https://tradernet.global/api/getPositionJson")
    assert str(error.value) == code
    assert "SECRET" not in str(error.value)


def test_only_exact_read_command_v3_and_urls_are_allowed():
    api = module._FreedomTradernet("p", "s", 2)
    with pytest.raises(FreedomPortfolioError):
        api.authorized_request("buy", {}, version=3)
    with pytest.raises(FreedomPortfolioError):
        api.authorized_request("getPositionJson", {}, version=2)
    for url in ("https://evil.test/api/getPositionJson", "https://tradernet.global.evil/api/getPositionJson",
                "http://tradernet.global/api/getPositionJson", "https://tradernet.global/api/buy"):
        with pytest.raises(FreedomPortfolioError):
            api.request("post", url)
    api.close()


@pytest.mark.parametrize("bad", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_timeout_rejected_before_client_use(bad):
    fake = FakeClient()
    with pytest.raises(ValueError):
        module.fetch_freedom_portfolio(client=fake, timeout_seconds=bad)
    assert not fake.calls


def test_bad_envelopes_raise_safe_error_and_quote_batch_failure_keeps_fallback():
    with pytest.raises(FreedomPortfolioError):
        module.fetch_freedom_portfolio(client=FakeClient({"result": {"error": "secret"}}))
    fake = FakeClient(snapshot([stock("A.US", price="8"), stock("B.US", price="7")]),
                      quote_errors=[("A.US", "B.US")])
    result = module.fetch_freedom_portfolio(client=fake)
    assert [position.value for position in result.positions] == [8, 7]
    assert all(position.price_source == "portfolio_fallback" for position in result.positions)
    assert result.warnings == ("quote_batch_unavailable",)


@pytest.mark.parametrize("bad_snapshot", [
    {"result": {"ps": {"loaded": True, "pos": [stock("A.US")]} }},
    {"result": {"ps": {"loaded": True, "pos": [stock("A.US")], "acc": [{"curr": "", "s": 1}]}}},
    {"result": {"ps": {"loaded": True, "pos": [{**stock("A.US"), "ticker": " "}], "acc": []}}},
    {"code": 17, "result": {"ps": {"loaded": True, "pos": [stock("A.US")], "acc": []}}},
    {"result": {"code": 17, "ps": {"loaded": True, "pos": [stock("A.US")], "acc": []}}},
])
def test_invalid_snapshot_is_rejected_before_any_quote_call(bad_snapshot):
    fake = FakeClient(bad_snapshot)
    with pytest.raises(FreedomPortfolioError):
        module.fetch_freedom_portfolio(client=fake)
    assert fake.calls == [("snapshot",)]


@pytest.mark.parametrize("level", ["top", "result", "ps"])
def test_errmsg_envelopes_are_rejected_before_any_quote_call(level):
    ps = {"loaded": True, "pos": [stock("A.US")], "acc": []}
    response = {"result": {"ps": ps}}
    if level == "top":
        response["errMsg"] = "private API failure"
    elif level == "result":
        response["result"]["errMsg"] = "private API failure"
    else:
        response["result"]["ps"]["errMsg"] = "private API failure"
    fake = FakeClient(response)
    with pytest.raises(FreedomPortfolioError):
        module.fetch_freedom_portfolio(client=fake)
    assert fake.calls == [("snapshot",)]


def test_malformed_quote_payload_is_safe_and_falls_back():
    class Malformed(FakeClient):
        def get_quotes(self, tickers):
            return {"result": {"quotes": "private junk"}}
    result = module.fetch_freedom_portfolio(client=Malformed(snapshot([stock("A.US")])))
    assert result.positions[0].price_source == "portfolio_fallback"


def test_refused_trade_command_never_reaches_transport():
    api = module._FreedomTradernet("p", "s", 2)
    called = False
    def no_network(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError
    api._freedom_session.request = no_network
    with pytest.raises(FreedomPortfolioError):
        api.authorized_request("buy", {})
    assert not called
    api.close()


def test_owned_client_reads_keys_lazily_and_closes_session(monkeypatch):
    monkeypatch.delenv("TRADERNET_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("TRADERNET_PRIVATE_KEY", raising=False)
    with pytest.raises(FreedomPortfolioError):
        module.fetch_freedom_portfolio()
    assert not hasattr(module, "_owned_session")
    instances = []
    class Owned(FakeClient):
        def __init__(self, *args):
            super().__init__()
            self.args = args
            instances.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setenv("TRADERNET_PUBLIC_KEY", "PUB_SECRET")
    monkeypatch.setenv("TRADERNET_PRIVATE_KEY", "PRIV_SECRET")
    monkeypatch.setattr(module, "_FreedomTradernet", Owned)
    module.fetch_freedom_portfolio()
    assert instances[0].closed and instances[0].args == ("PUB_SECRET", "PRIV_SECRET", 15.0)
    fake = FakeClient()
    module.fetch_freedom_portfolio(client=fake)
    assert not fake.closed


def test_socket_access_blocked_for_fake_client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("socket access")
    monkeypatch.setattr(socket, "socket", forbidden)
    assert module.fetch_freedom_portfolio(client=FakeClient()).complete


def test_error_chain_and_logging_do_not_disclose_secrets(caplog):
    class Failure(FakeClient):
        def account_summary(self):
            raise RuntimeError("PUB_SECRET PRIV_SECRET raw response")
    with caplog.at_level(logging.DEBUG), pytest.raises(FreedomPortfolioError) as error:
        module.fetch_freedom_portfolio(client=Failure())
    assert error.value.__cause__ is None
    assert "SECRET" not in caplog.text
    assert str(error.value) == "portfolio_fetch_failed"
