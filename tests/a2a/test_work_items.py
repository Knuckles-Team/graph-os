"""GRAPHOS-FLEET-R005.1: typed WorkItem view, producer slice for R005.

Native library tests: a durable work-item record's required identity
fields project into a typed, validated ``WorkItemView``, and a malformed
record is refused (single get) or dropped (listing) rather than silently
producing a partially typed result.
"""

from __future__ import annotations

import pytest

from graph_os.a2a.work_items import (
    WorkItemView,
    list_work_item_views,
    parse_work_item_view,
    work_item_view,
)

_RECORD = {
    "work_item_id": "workitem:orchestrator:a2a-deadbeef",
    "kind": "orchestrator_task",
    "tenant": "t1",
    "created_by": "actor-ref-1",
    "queue": "orchestrator_task",
    "state": "running",
    "metadata": {"a2a_schema": "graph-os-a2a-unary-v1"},
}


@pytest.mark.spec("GRAPHOS-FLEET-R005.1")
def test_projects_a_real_record_into_a_typed_view() -> None:
    view = work_item_view(_RECORD)
    assert view == WorkItemView(
        work_item_id="workitem:orchestrator:a2a-deadbeef",
        kind="orchestrator_task",
        tenant="t1",
        created_by="actor-ref-1",
        queue="orchestrator_task",
        state="running",
        metadata={"a2a_schema": "graph-os-a2a-unary-v1"},
    )


@pytest.mark.spec("GRAPHOS-FLEET-R005.1")
def test_optional_fields_default_when_absent() -> None:
    minimal = {
        "work_item_id": "workitem:orchestrator:x",
        "kind": "orchestrator_task",
        "tenant": "t1",
        "created_by": "actor-ref-1",
    }
    view = work_item_view(minimal)
    assert view.queue is None
    assert view.state is None
    assert view.metadata == {}


@pytest.mark.spec("GRAPHOS-FLEET-R005.1")
@pytest.mark.parametrize("field_name", ["work_item_id", "kind", "tenant", "created_by"])
def test_missing_required_field_is_refused(field_name: str) -> None:
    record = {k: v for k, v in _RECORD.items() if k != field_name}
    with pytest.raises(ValueError, match=field_name):
        work_item_view(record)


@pytest.mark.parametrize("field_name", ["work_item_id", "kind", "tenant", "created_by"])
def test_blank_required_field_is_refused(field_name: str) -> None:
    record = {**_RECORD, field_name: "   "}
    with pytest.raises(ValueError, match=field_name):
        work_item_view(record)


def test_non_mapping_metadata_is_refused() -> None:
    with pytest.raises(ValueError, match="metadata"):
        work_item_view({**_RECORD, "metadata": "not-a-mapping"})


def test_parse_reports_a_malformed_record_as_absent() -> None:
    assert parse_work_item_view({**_RECORD, "tenant": ""}) is None
    assert parse_work_item_view(_RECORD) is not None


def test_list_drops_malformed_records_and_keeps_order() -> None:
    second = {**_RECORD, "work_item_id": "workitem:orchestrator:second"}
    broken = {**_RECORD, "kind": ""}
    views = list_work_item_views([_RECORD, broken, second])
    assert [v.work_item_id for v in views] == [
        _RECORD["work_item_id"],
        second["work_item_id"],
    ]
