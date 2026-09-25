"""Canonical, byte-stable registry projection and SHA-256 digest."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .spec import EgSchemaRef, OpSpec


def _eg_schema(model: EgSchemaRef, contract_root: Path | None) -> dict[str, str]:
    filename, separator, pointer = model.path.partition("#")
    relative = Path(filename.removeprefix("contract/"))
    if (
        not separator
        or not pointer.startswith("/")
        or not filename.startswith("contract/schemas/")
        or ".." in relative.parts
    ):
        raise ValueError(f"invalid EG schema reference: {model.path!r}")
    root = contract_root or Path(
        str(importlib.resources.files("epistemic_graph") / "contract")
    )
    document = json.loads((root / relative).read_bytes())
    selected: Any = document
    for segment in pointer.lstrip("/").split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(selected, dict) or segment not in selected:
            raise ValueError(f"missing EG schema pointer: {model.path!r}")
        selected = selected[segment]
    content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return {
        "eg_schema": model.path,
        "schema_sha256": hashlib.sha256(content).hexdigest(),
    }


def _schema(
    model: type[BaseModel] | EgSchemaRef, contract_root: Path | None
) -> dict[str, Any]:
    if isinstance(model, EgSchemaRef):
        return _eg_schema(model, contract_root)
    return {
        "model": f"{model.__module__}.{model.__qualname__}",
        "schema": model.model_json_schema(),
    }


def canonical_op(op: OpSpec, *, contract_root: Path | None = None) -> dict[str, Any]:
    """Project every wire- or authority-relevant declaration field."""

    data = op.model_dump(mode="json", exclude={"params", "result"})
    data["params"] = _schema(op.params, contract_root)
    data["result"] = _schema(op.result, contract_root)
    data["scopes"] = sorted(op.scopes)
    data["executor_scopes"] = sorted(op.executor_scopes)
    data["surfaces"] = sorted(op.surfaces)
    return data


def canonical_registry(
    ops: Iterable[OpSpec], *, api_version: str = "1", contract_root: Path | None = None
) -> bytes:
    """Return the canonical UTF-8 registry document used by generators and runtime."""

    rows = [
        canonical_op(op, contract_root=contract_root)
        for op in sorted(ops, key=lambda item: item.id)
    ]
    return json.dumps(
        {"api_version": api_version, "ops": rows},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def registry_digest(
    ops: Iterable[OpSpec], *, api_version: str = "1", contract_root: Path | None = None
) -> str:
    return hashlib.sha256(
        canonical_registry(ops, api_version=api_version, contract_root=contract_root)
    ).hexdigest()
