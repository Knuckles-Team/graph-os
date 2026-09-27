"""The deployed runner never sends its process bearer to an untrusted child."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from agent_connector_sdk.ports.session import TransportEndpoint

import graph_os.connector_sync_entrypoint as entrypoint


@dataclass(frozen=True)
class _Services:
    endpoints: object


def _factory(monkeypatch, url: str):
    auth = object()
    monkeypatch.setattr(
        entrypoint,
        "default_services",
        lambda _settings, **_kwargs: _Services(
            endpoints=lambda _descriptor: TransportEndpoint(url=url)
        ),
    )
    service = entrypoint._fleet_services_factory(auth, object())(object())
    return service.endpoints, auth


def test_exact_tls_fleet_origin_receives_refreshable_auth(monkeypatch) -> None:
    endpoint, auth = _factory(monkeypatch, "https://example-source-mcp.arpa/mcp")
    result = endpoint(SimpleNamespace(connector="example-source-mcp"))
    assert result.auth is auth
    assert result.bearer_token == ""


@pytest.mark.parametrize(
    "configured_auth",
    ({"auth": object()}, {"bearer_token": "unverified-token"}),
)
def test_fleet_descriptor_cannot_override_verified_identity(
    monkeypatch, configured_auth: dict[str, object]
) -> None:
    monkeypatch.setattr(
        entrypoint,
        "default_services",
        lambda _settings, **_kwargs: _Services(
            endpoints=lambda _descriptor: TransportEndpoint(
                url="https://example-source-mcp.arpa/mcp", **configured_auth
            )
        ),
    )
    endpoints = entrypoint._fleet_services_factory(object(), object())(
        object()
    ).endpoints
    with pytest.raises(PermissionError, match="override verified runner identity"):
        endpoints(SimpleNamespace(connector="example-source-mcp"))


@pytest.mark.parametrize(
    "url",
    (
        "http://example-source-mcp.arpa/mcp",
        "https://example-other-mcp.arpa/mcp",
        "https://example-source-mcp.arpa:8443/mcp",
        "https://example-source-mcp.arpa/other",
        "https://example-source-mcp.arpa/mcp?target=other",
    ),
)
def test_untrusted_fleet_origins_never_receive_auth(monkeypatch, url: str) -> None:
    endpoint, _auth = _factory(monkeypatch, url)
    with pytest.raises(PermissionError, match="outside TLS identity boundary"):
        endpoint(SimpleNamespace(connector="example-source-mcp"))
