"""Refusal tests for the .1 typed models: R004, R006, R007."""

import pytest

from graph_os.identity.admin_console import AdminConsoleTab
from graph_os.identity.bootstrap import LoopbackBootstrapPrincipal
from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.mfa import MfaEnrollment


@pytest.mark.spec(
    "GRAPHOS-IDENTITY-R004.1", "GRAPHOS-IDENTITY-R006.1", "GRAPHOS-IDENTITY-R007.1"
)
def test_bootstrap_accepts_loopback() -> None:
    assert LoopbackBootstrapPrincipal(bind_host="127.0.0.1").bind_host == "127.0.0.1"


@pytest.mark.spec(
    "GRAPHOS-IDENTITY-R004.1", "GRAPHOS-IDENTITY-R006.1", "GRAPHOS-IDENTITY-R007.1"
)
def test_bootstrap_refuses_non_loopback_without_acknowledgment() -> None:
    with pytest.raises(IdentityUnavailable):
        LoopbackBootstrapPrincipal(bind_host="10.0.0.5")


@pytest.mark.spec(
    "GRAPHOS-IDENTITY-R004.1", "GRAPHOS-IDENTITY-R006.1", "GRAPHOS-IDENTITY-R007.1"
)
def test_bootstrap_allows_non_loopback_with_acknowledgment() -> None:
    principal = LoopbackBootstrapPrincipal(
        bind_host="10.0.0.5", exposure_acknowledged=True
    )
    assert principal.exposure_acknowledged is True


def test_bootstrap_refuses_empty_host() -> None:
    with pytest.raises(IdentityUnavailable):
        LoopbackBootstrapPrincipal(bind_host="")


def test_mfa_enrollment_accepts_known_methods() -> None:
    enrollment = MfaEnrollment(principal_id="p1", methods=frozenset({"totp"}))
    assert "totp" in enrollment.methods


def test_mfa_enrollment_refuses_unknown_method() -> None:
    with pytest.raises(IdentityUnavailable):
        MfaEnrollment(principal_id="p1", methods=frozenset({"sms"}))


def test_mfa_enrollment_refuses_required_group_with_no_methods() -> None:
    with pytest.raises(IdentityUnavailable):
        MfaEnrollment(principal_id="p1", required_group="admins")


def test_admin_console_tab_accepts_known_name() -> None:
    assert AdminConsoleTab(name="policy").name == "policy"


def test_admin_console_tab_refuses_unknown_name() -> None:
    with pytest.raises(IdentityUnavailable):
        AdminConsoleTab(name="billing")
