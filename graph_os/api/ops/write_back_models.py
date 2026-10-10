"""Typed write-back request/preview/receipt models (GRAPHOS-FLEET-R003.1).

Pure pydantic models for the D18 write-back producer: a caller first
previews a write-back (``WriteBackPreviewParams`` -> ``WriteBackPreview``),
then commits it (``WriteBackCommitParams`` -> ``WriteBackReceipt``). The
commit's idempotency key must match the key on the preview it is
committing, so a stray or replayed commit against the wrong preview is
refused rather than silently applied.

No registry or tool wiring lives here yet (that is GRAPHOS-FLEET-R003.2);
this module is deliberately free of any ``OpSpec``/registry import so it
can be reviewed and tested in isolation, and so it merges cleanly with the
``graph_os/api/ops/write_back.py`` module introduced by a parallel PR.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def derive_write_back_idempotency_key(
    connector_id: str,
    operation: str,
    payload: dict[str, Any],
) -> str:
    """Derive a stable idempotency key for a write-back of ``payload``.

    The key is a SHA-256 hex digest over ``connector_id``, ``operation``
    and a canonical (sorted-key) JSON encoding of ``payload``. Identical
    inputs always derive the same key; any change to any of the three
    inputs changes the key.
    """
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest_input = "\x1e".join((connector_id, operation, canonical_payload))
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


class WriteBackPreviewParams(_Model):
    """Request to preview a write-back before it is committed."""

    connector_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class WriteBackPreview(_Model):
    """The result of previewing a write-back.

    ``idempotency_key`` is derived from the preview's own
    ``connector_id``/``operation``/``payload`` via
    ``derive_write_back_idempotency_key`` and must be echoed back,
    unchanged, on the matching ``WriteBackCommitParams``.
    """

    connector_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class WriteBackCommitParams(_Model):
    """Request to commit a previously previewed write-back.

    ``idempotency_key`` must equal the ``idempotency_key`` on the
    ``WriteBackPreview`` this commit is applying; a mismatch means the
    commit does not correspond to that preview (a stale preview, a
    tampered payload, or a commit aimed at the wrong preview entirely)
    and must be refused rather than applied.
    """

    connector_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1)


class WriteBackReceipt(_Model):
    """The durable result of a committed write-back."""

    connector_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    applied: bool = True


class WriteBackIdempotencyMismatchError(ValueError):
    """Raised when a commit's idempotency key does not match its preview."""


def commit_write_back(
    preview: WriteBackPreview, commit: WriteBackCommitParams
) -> WriteBackReceipt:
    """Produce a receipt for ``commit`` against the ``preview`` it targets.

    Refuses (raises ``WriteBackIdempotencyMismatchError``) when
    ``commit.idempotency_key`` does not match ``preview.idempotency_key``,
    so a commit can never be applied against a preview it was not derived
    from.
    """
    if commit.idempotency_key != preview.idempotency_key:
        raise WriteBackIdempotencyMismatchError(
            "write-back commit idempotency key does not match its preview"
        )
    return WriteBackReceipt(
        connector_id=commit.connector_id,
        operation=commit.operation,
        idempotency_key=commit.idempotency_key,
        applied=True,
    )
