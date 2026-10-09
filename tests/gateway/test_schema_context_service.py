"""Tests for the schema-context models and application service
(GRAPHOS-DATA-MARKET-R005, GDM-02; SC-01/SC-04 in
``specs/data-and-market-projections/test-spec.md``).
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from graph_os.api.errors import GraphOSErrorCode, GraphOSRefusal
from graph_os.gateway.schema_context_service import get_schema_context
from graph_os.gateway.schemas.schema_context import SchemaContextRequest


def _valid_payload() -> dict[str, object]:
    return {
        "source_id": "sample_app",
        "schema": "public",
        "object": "customer",
        "intent": "definition",
    }


def test_request_accepts_a_valid_payload() -> None:
    request = SchemaContextRequest.model_validate(_valid_payload())
    assert request.source_id == "sample_app"
    assert request.schema_name == "public"
    assert request.depth == 0
    assert request.limit == 20


def test_request_rejects_unknown_intent() -> None:
    payload = _valid_payload() | {"intent": "delete"}
    with pytest.raises(ValidationError):
        SchemaContextRequest.model_validate(payload)


def test_request_rejects_oversized_limit() -> None:
    payload = _valid_payload() | {"limit": 101}
    with pytest.raises(ValidationError):
        SchemaContextRequest.model_validate(payload)


def test_request_rejects_oversized_depth() -> None:
    payload = _valid_payload() | {"depth": 4}
    with pytest.raises(ValidationError):
        SchemaContextRequest.model_validate(payload)


def test_request_rejects_unknown_field() -> None:
    payload = _valid_payload() | {"unexpected": "value"}
    with pytest.raises(ValidationError):
        SchemaContextRequest.model_validate(payload)


def test_get_schema_context_refuses_unavailable_with_zero_engine_calls() -> None:
    """SC-04: a missing engine capability yields typed UNAVAILABLE and the
    engine adapter receives no dispatch at all."""

    request = SchemaContextRequest.model_validate(_valid_payload())
    engine = Mock(spec=[])

    with pytest.raises(GraphOSRefusal) as excinfo:
        get_schema_context(engine, request)

    assert excinfo.value.code == GraphOSErrorCode.UNAVAILABLE
    assert engine.mock_calls == []


def test_get_schema_context_dispatches_through_the_published_capability() -> None:
    request = SchemaContextRequest.model_validate(_valid_payload())
    engine = Mock()
    engine.schema_context.schema_context.return_value = "sentinel-result"

    result = get_schema_context(engine, request)

    assert result == "sentinel-result"
    engine.schema_context.schema_context.assert_called_once_with(request)
