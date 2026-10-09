"""Focused tests for the .2 behavior slices: R004.2, R006.2, R007.2."""

import pytest

from graph_os.identity.admin_console import (
    AdminConsoleTab,
    dry_run_mapping,
    transition_mode_step,
)
from graph_os.identity.bootstrap import resolve_bootstrap_access, unsecured_mode_banner
from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.mfa import (
    MfaEnrollment,
    consume_recovery_code,
    enforce_group_requirement,
    verify_totp_step,
)


def test_resolve_bootstrap_access_allows_loopback_mutation() -> None:
    principal = resolve_bootstrap_access(
        bind_host="127.0.0.1",
        host_header="127.0.0.1",
        origin_header="http://127.0.0.1",
        is_mutating=True,
    )
    assert principal.bind_host == "127.0.0.1"


def test_resolve_bootstrap_access_refuses_origin_mismatch() -> None:
    with pytest.raises(IdentityUnavailable):
        resolve_bootstrap_access(
            bind_host="127.0.0.1",
            host_header="127.0.0.1",
            origin_header="http://evil.example",
            is_mutating=True,
        )


def test_unsecured_mode_banner_flags_exposed_bind() -> None:
    principal = resolve_bootstrap_access(
        bind_host="10.0.0.5",
        exposure_acknowledged=True,
        host_header="10.0.0.5",
    )
    banner = unsecured_mode_banner(principal)
    assert banner["doctor_check"] == "fail"
    assert banner["principal"] == "usr:bootstrap"


def test_verify_totp_step_refuses_replay() -> None:
    enrollment = MfaEnrollment(principal_id="p1", methods=frozenset({"totp"}))
    used: set[int] = set()
    verify_totp_step(enrollment, used, 42)
    with pytest.raises(IdentityUnavailable):
        verify_totp_step(enrollment, used, 42)


def test_consume_recovery_code_is_one_use() -> None:
    enrollment = MfaEnrollment(principal_id="p1", methods=frozenset({"recovery_code"}))
    used: set[str] = set()
    consume_recovery_code(enrollment, used, "abc123")
    with pytest.raises(IdentityUnavailable):
        consume_recovery_code(enrollment, used, "abc123")


def test_enforce_group_requirement_allows_enrolled_member() -> None:
    enrolled = MfaEnrollment(principal_id="p1", methods=frozenset({"totp"}))
    enforce_group_requirement("admins", frozenset({"admins"}), enrolled)  # no raise


def test_enforce_group_requirement_refuses_unenrolled_member() -> None:
    bare = MfaEnrollment(principal_id="p2")
    with pytest.raises(IdentityUnavailable):
        enforce_group_requirement("admins", frozenset({"admins"}), bare)


def test_enforce_group_requirement_ignores_non_member() -> None:
    bare = MfaEnrollment(principal_id="p3")
    enforce_group_requirement("admins", frozenset({"viewers"}), bare)  # no raise


def test_dry_run_mapping_returns_first_matching_rule() -> None:
    tab = AdminConsoleTab(name="providers")
    roles = dry_run_mapping(
        tab,
        [("is_admin", frozenset({"admin"})), ("is_member", frozenset({"member"}))],
        {"is_member": True},
    )
    assert roles == frozenset({"member"})


def test_dry_run_mapping_refuses_wrong_tab() -> None:
    with pytest.raises(IdentityUnavailable):
        dry_run_mapping(AdminConsoleTab(name="users"), [], {})


def test_transition_mode_step_confirms_target() -> None:
    step = transition_mode_step(AdminConsoleTab(name="policy"), "external")
    assert step == {"step": "confirm", "target_mode": "external"}


def test_transition_mode_step_refuses_unknown_mode() -> None:
    with pytest.raises(IdentityUnavailable):
        transition_mode_step(AdminConsoleTab(name="policy"), "bogus")
