"""Tests for the tenant-scoped market read service
(GRAPHOS-DATA-MARKET-R003; FI-01/FI-02 in
``specs/data-and-market-projections/test-spec.md``).
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from graph_os.api.errors import GraphOSErrorCode, GraphOSRefusal
from graph_os.finance.markets import (
    AccountBinding,
    MarketReadRequest,
    get_market_read,
)


def _request() -> MarketReadRequest:
    return MarketReadRequest(tenant_id="tenant-a", account_id="acct-1", symbol="AAPL")


def test_missing_binding_refuses_with_zero_provider_calls() -> None:
    provider = Mock(spec=[])

    with pytest.raises(GraphOSRefusal) as excinfo:
        get_market_read(provider, _request(), account_binding=None)

    assert excinfo.value.code == GraphOSErrorCode.UNAVAILABLE
    assert provider.mock_calls == []


def test_mismatched_tenant_refuses_with_zero_provider_calls() -> None:
    """FI-02: tenant B's binding cannot read tenant A's market data."""

    provider = Mock(spec=[])
    binding = AccountBinding(tenant_id="tenant-b", account_id="acct-1")

    with pytest.raises(GraphOSRefusal) as excinfo:
        get_market_read(provider, _request(), account_binding=binding)

    assert excinfo.value.code == GraphOSErrorCode.UNAVAILABLE
    assert provider.mock_calls == []


def test_mismatched_account_refuses_with_zero_provider_calls() -> None:
    provider = Mock(spec=[])
    binding = AccountBinding(tenant_id="tenant-a", account_id="acct-2")

    with pytest.raises(GraphOSRefusal):
        get_market_read(provider, _request(), account_binding=binding)

    assert provider.mock_calls == []


def test_missing_provider_capability_refuses_before_dispatch() -> None:
    provider = Mock(spec=[])
    binding = AccountBinding(tenant_id="tenant-a", account_id="acct-1")

    with pytest.raises(GraphOSRefusal) as excinfo:
        get_market_read(provider, _request(), account_binding=binding)

    assert excinfo.value.code == GraphOSErrorCode.UNAVAILABLE


def test_matched_binding_dispatches_through_the_provider() -> None:
    request = _request()
    binding = AccountBinding(tenant_id="tenant-a", account_id="acct-1")
    provider = Mock()
    sentinel = Mock()
    provider.market_read.return_value = sentinel

    result = get_market_read(provider, request, account_binding=binding)

    assert result is sentinel
    provider.market_read.assert_called_once_with(request)
