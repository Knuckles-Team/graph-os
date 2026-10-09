"""Typed model for optional multi-factor enrollment (GRAPHOS-IDENTITY-R006).

Slice .1: the typed model, construction validation, and refusal tests only.
The TOTP/WebAuthn/recovery-code ceremonies themselves are later slices.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import IdentityUnavailable

_METHODS = frozenset({"totp", "webauthn", "recovery_code"})


@dataclass(frozen=True, slots=True)
class MfaEnrollment:
    """One principal's MFA enrollment state for a configured group policy."""

    principal_id: str
    methods: frozenset[str] = field(default_factory=frozenset)
    required_group: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.principal_id, str) or not self.principal_id:
            raise IdentityUnavailable("MFA enrollment requires a principal id")
        unknown = self.methods - _METHODS
        if unknown:
            raise IdentityUnavailable(f"unknown MFA method(s): {sorted(unknown)}")
        if self.required_group is not None and not self.methods:
            raise IdentityUnavailable(
                "a required group may not enroll with zero MFA methods"
            )


def verify_totp_step(
    enrollment: MfaEnrollment, used_steps: set[int], step: int
) -> None:
    """Accept a TOTP time-step once; refuse an unenrolled method or a replay.

    Slice .2: the replay guard over the .1 typed enrollment model. The
    caller is responsible for computing ``step`` from the shared secret;
    this guards only the single-use property across a shared window set.
    """
    if "totp" not in enrollment.methods:
        raise IdentityUnavailable("totp is not an enrolled method")
    if step in used_steps:
        raise IdentityUnavailable("TOTP code already used in this window")
    used_steps.add(step)


def consume_recovery_code(
    enrollment: MfaEnrollment, used_codes: set[str], code: str
) -> None:
    """Consume a one-use recovery code; refuses replay or an unenrolled method."""
    if "recovery_code" not in enrollment.methods:
        raise IdentityUnavailable("recovery_code is not an enrolled method")
    if not code:
        raise IdentityUnavailable("recovery code required")
    if code in used_codes:
        raise IdentityUnavailable("recovery code already used")
    used_codes.add(code)


def enforce_group_requirement(
    required_group: str, member_groups: frozenset[str], enrollment: MfaEnrollment
) -> None:
    """Refuse when a member of ``required_group`` has not enrolled any method.

    ``required_group`` is the administrator-configured policy; ``enrollment``
    is the principal's own current state, which may legitimately have no
    ``required_group`` of its own while still being subject to this policy.
    """
    if required_group in member_groups and not enrollment.methods:
        raise IdentityUnavailable(
            f"MFA enrollment is required for group {required_group!r}"
        )
