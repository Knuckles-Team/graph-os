"""The GraphOS catalog boundary validates optional schema filters locally."""

from __future__ import annotations

import pytest

from graph_os.gateway.sql_catalog import SqlSchemaUnavailable, validate_schema_filter


@pytest.mark.parametrize("value", ["public", "_private9", "a" * 63])
def test_schema_filter_accepts_postgres_identifiers(value: str) -> None:
    assert validate_schema_filter(value) == value


@pytest.mark.parametrize("value", ["a" * 64, "é", "foo.bar", 'foo"bar', "a;drop"])
def test_schema_filter_refuses_unsafe_identifiers(value: str) -> None:
    with pytest.raises(SqlSchemaUnavailable) as refusal:
        validate_schema_filter(value)
    assert refusal.value.code == "invalid_schema_filter"
    assert refusal.value.status_code == 422
