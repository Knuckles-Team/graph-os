"""Fleet supervision is scoped by the verified GraphSession, not actor roles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.gateway import fleet


def _session(*, tenant: str, scopes: set[str], authenticated: bool = True):
    return SimpleNamespace(
        tenant=tenant,
        scopes=frozenset(scopes),
        actor=SimpleNamespace(authenticated=authenticated),
    )


def _bind(monkeypatch: pytest.MonkeyPatch, session: object) -> None:
    monkeypatch.setattr(
        "agent_utilities.api.session.resolve_session", lambda *a, **k: session
    )


def test_graph_admin_scope_sees_the_whole_fleet(monkeypatch) -> None:
    _bind(monkeypatch, _session(tenant="t1", scopes={"kg:admin"}))

    assert fleet._tenant_scope("sqlite") == ("", [])


def test_tenant_caller_is_restricted_to_its_own_tenant(monkeypatch) -> None:
    _bind(monkeypatch, _session(tenant="t1", scopes={"kg:read"}))

    predicate, params = fleet._tenant_scope("postgres")

    assert predicate.endswith("= ?")
    assert params == ["t1"]


@pytest.mark.parametrize(
    "session",
    [
        _session(tenant="", scopes={"kg:read"}),
        _session(tenant="t1", scopes={"kg:admin"}, authenticated=False),
    ],
)
def test_unverified_or_tenantless_callers_fail_closed(monkeypatch, session) -> None:
    _bind(monkeypatch, session)

    with pytest.raises(PermissionError):
        fleet._tenant_scope("sqlite")
