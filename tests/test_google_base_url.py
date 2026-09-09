"""Exercise real SDK URL construction, replacing only the HTTP transport."""

import os
import socket
from unittest import mock

import httpx
import pytest

from tradingagents.llm_clients.google_client import GoogleClient


@pytest.fixture(autouse=True)
def isolated_transport_environment(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Real network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    with mock.patch.dict(os.environ, {}, clear=True):
        yield


@pytest.mark.parametrize(
    ("base_url", "endpoint"),
    [
        ("http://localhost:8317/v1beta", "http://localhost:8317/v1beta"),
        ("http://localhost:8317/v1beta/", "http://localhost:8317/v1beta"),
        ("http://localhost:8317", "http://localhost:8317/v1beta"),
        ("http://localhost:8317/", "http://localhost:8317/v1beta"),
        ("https://gateway.invalid/google", "https://gateway.invalid/google/v1beta"),
        ("https://gateway.invalid/google/v1beta", "https://gateway.invalid/google/v1beta"),
        ("https://gateway.invalid/v1beta-proxy", "https://gateway.invalid/v1beta-proxy/v1beta"),
        (None, "https://generativelanguage.googleapis.com/v1beta"),
        ("", "https://generativelanguage.googleapis.com/v1beta"),
    ],
)
def test_sdk_constructs_single_version_url(monkeypatch, base_url, endpoint):
    requests = []

    def respond(transport, request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{
                    "content": {"role": "model", "parts": [{"text": "synthetic reply"}]},
                    "finishReason": "STOP",
                }],
            },
            request=request,
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    model = "gemini-3.8-flash-high"
    with pytest.warns(RuntimeWarning, match="Continuing anyway"):
        llm = GoogleClient(
            model, base_url=base_url, api_key="dummy-google-key",
            max_retries=0,
        ).get_llm()
    try:
        response = llm.invoke("Synthetic test prompt")
    finally:
        llm.client.close()

    assert response.content == "synthetic reply"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{endpoint}/models/{model}:generateContent"
    assert request.url.path.split("/").count("v1beta") == 1
    assert request.headers["x-goog-api-key"] == "dummy-google-key"
