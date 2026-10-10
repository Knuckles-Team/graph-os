"""Tests for the client entry-point token registry (GRAPHOS-IDENTITY-R017)."""

from __future__ import annotations

import pytest

from graph_os.identity.client_entry_points import (
    CLIENT_TOKEN_REQUIREMENTS,
    ClientEntryPoint,
    entry_points_without_verified_tokens,
)


@pytest.mark.spec("GRAPHOS-IDENTITY-R017")
def test_every_client_entry_point_is_registered() -> None:
    registered = {req.entry_point for req in CLIENT_TOKEN_REQUIREMENTS}

    assert registered == set(ClientEntryPoint)


@pytest.mark.spec("GRAPHOS-IDENTITY-R017")
def test_every_client_entry_point_carries_a_verified_token() -> None:
    # Empty once every client path has migrated -- the precondition
    # GRAPHOS-IDENTITY-R017 requires before GraphOS can stop relying on
    # the engine's unauthenticated opt-out.
    assert entry_points_without_verified_tokens() == ()

    for requirement in CLIENT_TOKEN_REQUIREMENTS:
        assert requirement.carries_verified_token is True
        assert requirement.token_kind in ("local-issuer", "service")
