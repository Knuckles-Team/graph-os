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
    endpoint, auth = _factory(monkeypatch, "https://source-mcp.arpa/mcp")
    result = endpoint(SimpleNamespace(connector="source-mcp"))
    assert result.auth is auth
    assert result.bearer_token == ""


@pytest.mark.parametrize(
    "url",
    (
        "http://source-mcp.arpa/mcp",
        "https://other-mcp.arpa/mcp",
        "https://source-mcp.arpa:8443/mcp",
        "https://source-mcp.arpa/other",
        "https://source-mcp.arpa/mcp?target=other",
    ),
)
def test_untrusted_fleet_origins_never_receive_auth(monkeypatch, url: str) -> None:
    endpoint, _auth = _factory(monkeypatch, url)
    with pytest.raises(PermissionError, match="outside TLS identity boundary"):
        endpoint(SimpleNamespace(connector="source-mcp"))
