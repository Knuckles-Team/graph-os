"""Tests for the typed write-back models (GRAPHOS-FLEET-R003.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph_os.api.ops.write_back_models import (
    WriteBackCommitParams,
    WriteBackIdempotencyMismatchError,
    WriteBackPreview,
    WriteBackPreviewParams,
    WriteBackReceipt,
    commit_write_back,
    derive_write_back_idempotency_key,
)


@pytest.mark.spec("GRAPHOS-FLEET-R003.1")
def test_idempotency_key_is_stable_for_identical_inputs() -> None:
    payload = {"issue": "GOS-1", "status": "done", "fields": {"a": 1, "b": 2}}
    key_a = derive_write_back_idempotency_key("jira", "update_issue", payload)
    key_b = derive_write_back_idempotency_key(
        "jira",
        "update_issue",
        {"fields": {"b": 2, "a": 1}, "status": "done", "issue": "GOS-1"},
    )
    assert key_a == key_b


@pytest.mark.spec("GRAPHOS-FLEET-R003.1")
def test_idempotency_key_is_sensitive_to_a_changed_input() -> None:
    base = derive_write_back_idempotency_key("jira", "update_issue", {"status": "done"})
    changed_payload = derive_write_back_idempotency_key(
        "jira", "update_issue", {"status": "open"}
    )
    changed_operation = derive_write_back_idempotency_key(
        "jira", "create_comment", {"status": "done"}
    )
    changed_connector = derive_write_back_idempotency_key(
        "github", "update_issue", {"status": "done"}
    )
    assert base not in {changed_payload, changed_operation, changed_connector}


@pytest.mark.spec("GRAPHOS-FLEET-R003.1")
def test_preview_params_reject_unknown_fields() -> None:
    WriteBackPreviewParams(connector_id="jira", operation="update_issue", payload={})
    with pytest.raises(ValidationError):
        WriteBackPreviewParams.model_validate(
            {
                "connector_id": "jira",
                "operation": "update_issue",
                "payload": {},
                "extra_field": "nope",
            }
        )


@pytest.mark.spec("GRAPHOS-FLEET-R003.1")
def test_preview_and_receipt_field_validation() -> None:
    payload = {"status": "done"}
    key = derive_write_back_idempotency_key("jira", "update_issue", payload)

    preview = WriteBackPreview(
        connector_id="jira",
        operation="update_issue",
        payload=payload,
        idempotency_key=key,
        summary="Set GOS-1 to done",
    )
    assert preview.idempotency_key == key

    with pytest.raises(ValidationError):
        WriteBackPreview(
            connector_id="",
            operation="update_issue",
            payload=payload,
            idempotency_key=key,
            summary="Set GOS-1 to done",
        )

    receipt = WriteBackReceipt(
        connector_id="jira",
        operation="update_issue",
        idempotency_key=key,
    )
    assert receipt.applied is True

    with pytest.raises(ValidationError):
        WriteBackReceipt(
            connector_id="jira", operation="update_issue", idempotency_key=""
        )


@pytest.mark.spec("GRAPHOS-FLEET-R003.1")
def test_commit_succeeds_when_key_matches_preview() -> None:
    payload = {"status": "done"}
    key = derive_write_back_idempotency_key("jira", "update_issue", payload)
    preview = WriteBackPreview(
        connector_id="jira",
        operation="update_issue",
        payload=payload,
        idempotency_key=key,
        summary="Set GOS-1 to done",
    )
    commit = WriteBackCommitParams(
        connector_id="jira",
        operation="update_issue",
        payload=payload,
        idempotency_key=key,
    )

    receipt = commit_write_back(preview, commit)

    assert receipt.idempotency_key == preview.idempotency_key
    assert receipt.applied is True


@pytest.mark.spec("GRAPHOS-FLEET-R003.1")
def test_commit_is_refused_when_key_does_not_match_preview() -> None:
    payload = {"status": "done"}
    preview = WriteBackPreview(
        connector_id="jira",
        operation="update_issue",
        payload=payload,
        idempotency_key=derive_write_back_idempotency_key(
            "jira", "update_issue", payload
        ),
        summary="Set GOS-1 to done",
    )
    mismatched_commit = WriteBackCommitParams(
        connector_id="jira",
        operation="update_issue",
        payload=payload,
        idempotency_key="not-the-preview-key",
    )

    with pytest.raises(WriteBackIdempotencyMismatchError):
        commit_write_back(preview, mismatched_commit)
