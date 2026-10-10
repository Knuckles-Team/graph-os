"""GRAPHOS-FLEET-R003.1: D18 write-back is typed, idempotent and preview-only."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph_os.api.ops.write_back import (
    WriteBackPreviewRequest,
    WriteBackRefused,
    preview_write_back,
)


def test_preview_returns_a_plan_not_an_applied_write() -> None:
    request = WriteBackPreviewRequest(
        connector="sql",
        target="customers.email",
        change={"value": "a@example.com"},
        idempotency_key="wb-1",
    )
    result = preview_write_back(request)
    assert result.applied is False
    assert result.would_apply == {"value": "a@example.com"}
    assert result.idempotency_key == "wb-1"


def test_preview_is_idempotent_for_the_same_key() -> None:
    request = WriteBackPreviewRequest(
        connector="sql",
        target="customers.email",
        change={"value": "a@example.com"},
        idempotency_key="wb-1",
    )
    first = preview_write_back(request)
    second = preview_write_back(request)
    assert first == second


def test_preview_refuses_blank_idempotency_key() -> None:
    request = WriteBackPreviewRequest(
        connector="sql",
        target="customers.email",
        change={"value": "a@example.com"},
        idempotency_key=" ",
    )
    with pytest.raises(WriteBackRefused):
        preview_write_back(request)


def test_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WriteBackPreviewRequest(
            connector="sql",
            target="customers.email",
            change={},
            idempotency_key="wb-1",
            apply=True,  # type: ignore[call-arg]
        )
