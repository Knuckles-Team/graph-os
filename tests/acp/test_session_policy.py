"""GRAPHOS-ACP-001 R001.1: ACP session-policy model and admission check."""

from datetime import UTC, datetime, timedelta

import pytest

from graph_os.acp import (
    AcpRefusal,
    AcpRefusalReason,
    AcpSessionAdmission,
    AcpSessionPolicy,
    PrincipalContext,
)

PRINCIPAL = PrincipalContext(
    principal_id="usr:alice", tenant="acme", scopes=frozenset({"chat:use"})
)
POLICY = AcpSessionPolicy(
    idle_limit=timedelta(minutes=10), absolute_limit=timedelta(hours=1)
)
T0 = datetime(2026, 10, 9, tzinfo=UTC)


def test_admit_refuses_without_principal() -> None:
    admission = AcpSessionAdmission(POLICY)
    with pytest.raises(AcpRefusal) as excinfo:
        admission.admit(None, chat_adapter_available=True, now=T0)
    assert excinfo.value.reason is AcpRefusalReason.UNAUTHENTICATED


@pytest.mark.spec("GRAPHOS-ACP-R001.1")
def test_admit_succeeds_and_binds_principal() -> None:
    admission = AcpSessionAdmission(POLICY)
    session = admission.admit(PRINCIPAL, chat_adapter_available=True, now=T0)
    assert session.principal == PRINCIPAL
    assert session.admitted_at == T0
    assert session.absolute_expires_at == T0 + POLICY.absolute_limit


def test_binding_is_immutable_across_sessions() -> None:
    admission = AcpSessionAdmission(POLICY)
    other = PrincipalContext(principal_id="usr:bob", tenant="acme", scopes=frozenset())
    session_a = admission.admit(PRINCIPAL, chat_adapter_available=True, now=T0)
    session_b = admission.admit(other, chat_adapter_available=True, now=T0)
    assert session_a.principal.principal_id == "usr:alice"
    assert session_b.principal.principal_id == "usr:bob"
    with pytest.raises(AttributeError):
        session_a.principal.principal_id = "usr:mallory"  # type: ignore[misc]


def test_check_message_refuses_when_idle_limit_exceeded() -> None:
    admission = AcpSessionAdmission(POLICY)
    session = admission.admit(PRINCIPAL, chat_adapter_available=True, now=T0)
    later = T0 + timedelta(minutes=11)
    with pytest.raises(AcpRefusal) as excinfo:
        admission.check_message(session, chat_adapter_available=True, now=later)
    assert excinfo.value.reason is AcpRefusalReason.IDLE_EXPIRED


def test_check_message_refuses_when_absolute_limit_exceeded_despite_activity() -> None:
    admission = AcpSessionAdmission(POLICY)
    session = admission.admit(PRINCIPAL, chat_adapter_available=True, now=T0)
    active = T0 + timedelta(minutes=5)
    session = admission.check_message(session, chat_adapter_available=True, now=active)
    past_absolute = T0 + timedelta(hours=1, minutes=1)
    with pytest.raises(AcpRefusal) as excinfo:
        admission.check_message(session, chat_adapter_available=True, now=past_absolute)
    assert excinfo.value.reason is AcpRefusalReason.ABSOLUTE_EXPIRED


@pytest.mark.spec("GRAPHOS-ACP-R001.1")
def test_admit_refuses_when_chat_adapter_unavailable() -> None:
    admission = AcpSessionAdmission(POLICY)
    with pytest.raises(AcpRefusal) as excinfo:
        admission.admit(PRINCIPAL, chat_adapter_available=False, now=T0)
    assert excinfo.value.reason is AcpRefusalReason.CHAT_ADAPTER_UNAVAILABLE


def test_check_message_refuses_when_chat_adapter_goes_unavailable() -> None:
    admission = AcpSessionAdmission(POLICY)
    session = admission.admit(PRINCIPAL, chat_adapter_available=True, now=T0)
    with pytest.raises(AcpRefusal) as excinfo:
        admission.check_message(
            session, chat_adapter_available=False, now=T0 + timedelta(minutes=1)
        )
    assert excinfo.value.reason is AcpRefusalReason.CHAT_ADAPTER_UNAVAILABLE


def test_check_message_touches_last_activity_on_success() -> None:
    admission = AcpSessionAdmission(POLICY)
    session = admission.admit(PRINCIPAL, chat_adapter_available=True, now=T0)
    active = T0 + timedelta(minutes=5)
    touched = admission.check_message(session, chat_adapter_available=True, now=active)
    assert touched.last_activity_at == active
    assert touched.admitted_at == session.admitted_at
