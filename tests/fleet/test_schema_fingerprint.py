"""GRAPHOS-FLEET-R002.1: tool schema fingerprints computed from served schemas.

Typed-model slice: every fleet tool pin's schema fingerprint is computed
from the tool's actual served input schema, never an empty placeholder.
``compute_schema_fingerprint`` refuses an empty schema outright, and
``items_from_child_probe`` populates ``CatalogItem.schema_fingerprint`` for
probed tool rows from their real served ``inputSchema`` rather than leaving
it defaulted or empty.
"""

from __future__ import annotations

import pytest

from graph_os.fleet.catalog_items import (
    compute_schema_fingerprint,
    items_from_child_probe,
)

_REAL_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}


@pytest.mark.spec("GRAPHOS-FLEET-R002.1")
def test_fingerprint_is_a_stable_sha256_of_the_real_schema() -> None:
    first = compute_schema_fingerprint(_REAL_SCHEMA)
    second = compute_schema_fingerprint(dict(_REAL_SCHEMA))
    assert first == second
    assert len(first) == 64
    assert all(ch in "0123456789abcdef" for ch in first)


@pytest.mark.spec("GRAPHOS-FLEET-R002.1")
def test_fingerprint_changes_with_the_served_schema() -> None:
    other = {**_REAL_SCHEMA, "required": []}
    assert compute_schema_fingerprint(_REAL_SCHEMA) != compute_schema_fingerprint(other)


@pytest.mark.spec("GRAPHOS-FLEET-R002.1")
def test_empty_schema_fingerprint_is_refused() -> None:
    with pytest.raises(ValueError, match="empty schema"):
        compute_schema_fingerprint({})


def test_probed_tool_gets_a_real_fingerprint_not_a_placeholder() -> None:
    (item,) = items_from_child_probe(
        "srv",
        {"tools": [{"name": "run", "description": "", "inputSchema": _REAL_SCHEMA}]},
    )
    assert item.schema_fingerprint == compute_schema_fingerprint(_REAL_SCHEMA)


def test_probed_tool_with_no_served_schema_gets_no_fingerprint() -> None:
    (item,) = items_from_child_probe(
        "srv", {"tools": [{"name": "run", "description": ""}]}
    )
    assert item.schema_fingerprint is None


def test_probed_non_tool_kinds_never_get_a_fingerprint() -> None:
    (item,) = items_from_child_probe(
        "srv",
        {
            "resources": [
                {"uri": "graphos://x", "description": "", "inputSchema": _REAL_SCHEMA}
            ]
        },
    )
    assert item.schema_fingerprint is None
