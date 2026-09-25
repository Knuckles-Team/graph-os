"""Outbound fleet-child service identity selected by MCP_CLIENT_AUTH."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import httpx2
import pytest

from graph_os.fleet import child_credentials as cc


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MCP_CLIENT_AUTH",
        "MCP_BASIC_AUTH_USERNAME",
        "MCP_BASIC_AUTH_PASSWORD_REF",
        "MCP_BEARER_TOKEN_FILE",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET_REF",
        "OIDC_AUDIENCE",
        "OIDC_TOKEN_URL",
        "OIDC_ISSUER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cc, "_OIDC_AUTH", None)


def _authorize(auth: Any) -> str:
    request = httpx.Request("GET", "https://child.example/mcp")
    flow = auth.auth_flow(request)
    return next(flow).headers["Authorization"]


def test_disabled_mode_attaches_nothing() -> None:
    assert cc.child_auth({}) is None
    assert cc.outbound_auth_configuration_status()["mode"] == "none"


def test_unknown_mode_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CLIENT_AUTH", "bearer-please")
    with pytest.raises(cc.ChildAuthConfigurationError):
        cc.child_auth({})


def test_incomplete_mode_fails_closed_and_reports_missing(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CLIENT_AUTH", "basic")
    status = cc.outbound_auth_configuration_status()
    assert status["ready"] is False
    assert "MCP_BASIC_AUTH_PASSWORD_REF" in status["missing"]
    with pytest.raises(cc.ChildAuthConfigurationError):
        cc.child_auth({})


def test_basic_resolves_its_secret_reference_per_request(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CLIENT_AUTH", "basic")
    monkeypatch.setenv("MCP_BASIC_AUTH_USERNAME", "svc")
    monkeypatch.setenv("MCP_BASIC_AUTH_PASSWORD_REF", "env://CHILD_BASIC_PW")
    monkeypatch.setenv("CHILD_BASIC_PW", "first")
    auth = cc.child_auth({})
    assert isinstance(auth, httpx.Auth) and isinstance(auth, httpx2.Auth)
    assert _authorize(auth) == "Basic " + base64.b64encode(b"svc:first").decode()
    monkeypatch.setenv("CHILD_BASIC_PW", "rotated")
    assert _authorize(auth).endswith(base64.b64encode(b"svc:rotated").decode())


def test_rotating_file_is_reread_and_must_be_private(monkeypatch, tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("one\n", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv("MCP_CLIENT_AUTH", "rotating-file-bearer")
    monkeypatch.setenv("MCP_BEARER_TOKEN_FILE", str(token))
    auth = cc.child_auth({})
    assert _authorize(auth) == "Bearer one"
    token.write_text("two", encoding="utf-8")
    assert _authorize(auth) == "Bearer two"
    token.chmod(0o644)
    with pytest.raises(cc.ChildAuthConfigurationError):
        _authorize(auth)
    assert cc.service_session_max_age({}) is None


def test_child_declared_authorization_is_never_overridden(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CLIENT_AUTH", "basic")
    assert cc.child_auth({"authorization": "Bearer child-own"}) is None


def test_oidc_session_lifetime_uses_assumed_ttl_before_first_mint(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_CLIENT_AUTH", "oidc-client-credentials")
    monkeypatch.setenv("OIDC_CLIENT_ID", "graph-os")
    monkeypatch.setenv("OIDC_CLIENT_SECRET_REF", "env://OIDC_TEST_SECRET")
    monkeypatch.setenv("OIDC_AUDIENCE", "agent-services")
    monkeypatch.setenv("OIDC_TOKEN_URL", "https://identity.example.test/token")
    assert cc.child_auth({}) is cc.child_auth({})
    assert cc.service_session_max_age({}) == 265.0
