"""GRAPHOS-FLEET-R004: pack annotations carry capability, digest, modality,
cost, latency.

A catalog audit: every fleet pack item's catalog entry built by
``connector_items`` carries its capability, schema digest, modality, cost and
latency annotations, populated across the whole fleet rather than a subset,
and an incomplete entry fails closed instead of publishing a partial row.
"""

from __future__ import annotations

import pytest

from graph_os.fleet.catalog_items import PACK_ANNOTATION_FIELDS, connector_items

_COMPLETE_ENTRY = {
    "pack": "p",
    "name": "sync",
    "op": "ingest.sources.sync",
    "capability": "ingest.sources.sync",
    "schema_digest": "sha256:" + "a" * 64,
    "modality": "text",
    "cost": "low",
    "latency": "sub-second",
}


def test_every_pack_item_carries_all_required_annotations_fleet_wide() -> None:
    entries = [
        {**_COMPLETE_ENTRY, "pack": "p1", "name": "one"},
        {**_COMPLETE_ENTRY, "pack": "p2", "name": "two", "modality": "image"},
        {**_COMPLETE_ENTRY, "pack": "p3", "name": "three", "cost": "high"},
    ]
    items = connector_items(entries)
    assert len(items) == 3
    for item in items:
        assert item.annotations is not None
        for field_name in PACK_ANNOTATION_FIELDS:
            assert field_name in item.annotations
            value = item.annotations[field_name]
            assert value not in (None, ""), f"{field_name} is empty for {item.id}"


def test_nested_annotations_mapping_is_also_accepted() -> None:
    entry = {
        "pack": "p",
        "name": "nested",
        "op": "ingest.sources.sync",
        "annotations": {
            "capability": "ingest.sources.sync",
            "schema_digest": "sha256:" + "b" * 64,
            "modality": "text",
            "cost": "low",
            "latency": "sub-second",
        },
    }
    (item,) = connector_items([entry])
    assert item.annotations == entry["annotations"]


@pytest.mark.parametrize("missing_field", PACK_ANNOTATION_FIELDS)
def test_missing_annotation_field_fails_closed(missing_field: str) -> None:
    entry = {k: v for k, v in _COMPLETE_ENTRY.items() if k != missing_field}
    with pytest.raises(ValueError, match=missing_field):
        connector_items([entry])


@pytest.mark.parametrize("empty_field", PACK_ANNOTATION_FIELDS)
def test_empty_string_annotation_field_fails_closed(empty_field: str) -> None:
    entry = {**_COMPLETE_ENTRY, empty_field: ""}
    with pytest.raises(ValueError, match=empty_field):
        connector_items([entry])
