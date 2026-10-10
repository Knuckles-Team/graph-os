"""Tests for the R005 approved action-to-operation mapping."""

from __future__ import annotations

import pytest

from graph_os.mcp_server.legacy_action_mapping import (
    APPROVED_ACTION_MAPPING,
    ActionOperationMapping,
    DuplicateActionMappingError,
    UnmappedActionError,
    build_approved_action_mapping,
    validate_served_actions,
)


@pytest.mark.spec("GRAPHOS-HOST-R005.1")
def test_approved_mapping_is_non_empty_and_unique() -> None:
    assert APPROVED_ACTION_MAPPING
    assert len(set(APPROVED_ACTION_MAPPING.values())) == len(APPROVED_ACTION_MAPPING)


def test_served_actions_within_the_approved_table_pass() -> None:
    served = tuple(APPROVED_ACTION_MAPPING.keys())
    validate_served_actions(served, APPROVED_ACTION_MAPPING)


@pytest.mark.spec("GRAPHOS-HOST-R005.1")
def test_unmapped_served_action_is_refused() -> None:
    served = (*APPROVED_ACTION_MAPPING.keys(), "some_retired_action_with_no_mapping")
    with pytest.raises(UnmappedActionError):
        validate_served_actions(served, APPROVED_ACTION_MAPPING)


@pytest.mark.spec("GRAPHOS-HOST-R005.1")
def test_duplicate_action_in_table_construction_is_refused() -> None:
    with pytest.raises(DuplicateActionMappingError):
        build_approved_action_mapping(
            (
                ActionOperationMapping("code_context", "graph_code.code_context"),
                ActionOperationMapping("code_context", "graph_code.other_operation"),
            )
        )
