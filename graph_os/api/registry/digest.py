"""Canonical, byte-stable registry projection and SHA-256 digest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from .spec import EgSchemaRef, OpSpec


def _schema(model: type[BaseModel] | EgSchemaRef) -> dict[str, Any]:
    if isinstance(model, EgSchemaRef):
        return {"eg_schema": model.path}
    return {
        "model": f"{model.__module__}.{model.__qualname__}",
        "schema": model.model_json_schema(),
    }


def canonical_op(op: OpSpec) -> dict[str, Any]:
    """Project every wire- or authority-relevant declaration field."""

    data = op.model_dump(mode="json", exclude={"params", "result"})
    data["params"] = _schema(op.params)
    data["result"] = _schema(op.result)
    data["scopes"] = sorted(op.scopes)
    data["executor_scopes"] = sorted(op.executor_scopes)
    data["surfaces"] = sorted(op.surfaces)
    return data


def canonical_registry(ops: Iterable[OpSpec], *, api_version: str = "1") -> bytes:
    """Return the canonical UTF-8 registry document used by generators and runtime."""

    rows = [canonical_op(op) for op in sorted(ops, key=lambda item: item.id)]
    return json.dumps(
        {"api_version": api_version, "ops": rows},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def registry_digest(ops: Iterable[OpSpec], *, api_version: str = "1") -> str:
    return hashlib.sha256(canonical_registry(ops, api_version=api_version)).hexdigest()
