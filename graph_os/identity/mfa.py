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
