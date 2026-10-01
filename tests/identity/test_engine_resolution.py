"""The EG session MFA timestamp is admitted only as typed, verified data."""

from __future__ import annotations

import pytest

from graph_os.identity.engine import IdentityUnavailable, Resolution

BASE = {
    "principal_id": "usr:alice",
    "username": "alice",
    "kind": "human",
    "status": "active",
}


def test_resolution_parses_verified_mfa_timestamp() -> None:
    assert Resolution.parse(BASE).session_mfa_at_ms is None
    assert Resolution.parse({**BASE, "session_mfa_at_ms": 0}).session_mfa_at_ms == 0
    assert (
        Resolution.parse(
            {**BASE, "session_mfa_at_ms": 1_900_000_000_000}
        ).session_mfa_at_ms
        == 1_900_000_000_000
    )


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "1900000000000", []])
def test_resolution_rejects_invalid_mfa_timestamp(invalid: object) -> None:
    with pytest.raises(IdentityUnavailable, match="MFA time is invalid"):
        Resolution.parse({**BASE, "session_mfa_at_ms": invalid})
