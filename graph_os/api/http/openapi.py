"""Caller-filtered OpenAPI projection of the shared operation registry."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _schema(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    if isinstance(model, dict):
        return model
    return {"type": "object"}


def document(ops: Iterable[Any], *, digest: str) -> dict[str, Any]:
    """Build a document containing only the operations visible to this caller."""
    paths: dict[str, dict[str, Any]] = {}
    for op in ops:
        shapes = [("POST", f"/api/v1/ops/{op.id}")]
        if op.http is not None:
            shapes.append((str(op.http.method).upper(), op.http.path))
        for method, path in shapes:
            operation: dict[str, Any] = {
                "operationId": op.id,
                "summary": op.summary,
                "security": [{"bearerAuth": []}, {"sessionCookie": []}],
                "responses": {
                    "200": {
                        "description": "Operation result",
                        "content": {"application/json": {"schema": _schema(op.result)}},
                    }
                },
            }
            if method != "GET":
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": _schema(op.params)}},
                }
            paths.setdefault(path, {})[method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": "GraphOS API", "version": "v1"},
        "x-registry-digest": digest,
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"},
                "sessionCookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "__Host-graphos-session",
                },
            }
        },
    }
