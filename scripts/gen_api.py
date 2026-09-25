#!/usr/bin/env python3
"""Generate the versioned GraphOS API contract from its declared registry.

The EG error catalog is a required input. A missing registry or catalog fails
closed; neither an empty operation set nor a hand-maintained error map is an
acceptable published API contract.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "graph_os/api/generated"
sys.path.insert(0, str(ROOT))


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()


def _registry(module_name: str) -> dict[str, Any]:
    module = importlib.import_module(module_name)
    source = getattr(module, "get_registry", None)
    if source is None:
        source = getattr(module, "REGISTRY", None)
    if source is None:
        raise ValueError(f"{module_name} has no get_registry or REGISTRY")
    registry = source() if callable(source) else source
    document = json.loads(registry.canonical)
    if not document.get("ops"):
        raise ValueError("refusing to generate an empty API registry")
    document["registry_digest"] = registry.digest
    return document


def _engine_errors(path: Path) -> bytes:
    raw = path.read_bytes()
    contract = json.loads(raw)
    if not isinstance(contract, dict) or not isinstance(contract.get("errors"), list):
        raise ValueError("EG errors.json must contain an errors array")
    version = contract.get("contract_version")
    if not isinstance(version, (int, str)) or isinstance(version, bool):
        raise ValueError("EG errors.json has no contract_version")
    rows: dict[str, tuple[int, bool]] = {}
    for row in contract["errors"]:
        if not isinstance(row, dict):
            raise ValueError("EG error row must be an object")
        code = row.get("code")
        status = row.get("http_status_hint")
        retryable = row.get("retryable")
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(status, int)
            or isinstance(status, bool)
            or not 400 <= status <= 599
            or not isinstance(retryable, bool)
        ):
            raise ValueError(f"invalid EG error row: {row!r}")
        if code in rows:
            raise ValueError(f"duplicate EG error code: {code}")
        rows[code] = (status, retryable)
    if not rows:
        raise ValueError("refusing to generate an empty EG error map")
    lines = [
        '"""Generated from epistemic_graph/contract/errors.json. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        f"SOURCE_CONTRACT_VERSION = {version!r}",
        f"SOURCE_SHA256 = {hashlib.sha256(raw).hexdigest()!r}",
        "ENGINE_ERRORS: dict[str, tuple[int, bool]] = {",
    ]
    lines.extend(f"    {code!r}: {rows[code]!r}," for code in sorted(rows))
    return ("\n".join([*lines, "}", ""])).encode()


def _descriptors(registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_version": registry["api_version"],
        "registry_digest": registry["registry_digest"],
        "ops": [
            {
                "id": op["id"],
                "verb": op["verb"],
                "summary": op["summary"],
                "examples": op["examples"],
                "domain": op["id"].split(".", 1)[0],
                "tags": [op["id"].split(".", 1)[0]],
            }
            for op in registry["ops"]
        ],
    }


def _schema(value: dict[str, Any]) -> dict[str, Any]:
    if "schema" in value:
        return value["schema"]
    if "eg_schema" in value:
        return {"$ref": f"/api/v1/eg-schemas/{value['eg_schema']}"}
    raise ValueError(f"unknown schema reference: {value!r}")


def _openapi(registry: dict[str, Any]) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for op in registry["ops"]:
        http = op.get("http") or {"method": "POST", "path": f"/api/v1/ops/{op['id']}"}
        path = http["path"]
        method = http["method"].lower()
        if path in paths and method in paths[path]:
            raise ValueError(f"duplicate HTTP route: {method.upper()} {path}")
        paths.setdefault(path, {})[method] = {
            "operationId": op["id"],
            "summary": op["summary"],
            "tags": [op["id"].split(".", 1)[0]],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": _schema(op["params"])}},
            },
            "responses": {
                "200": {
                    "description": "Operation result",
                    "content": {"application/json": {"schema": _schema(op["result"])}},
                },
                "default": {"description": "GraphOS error envelope"},
            },
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "GraphOS API", "version": str(registry["api_version"])},
        "x-registry-digest": registry["registry_digest"],
        "paths": paths,
    }


def _client_models(registry: dict[str, Any]) -> bytes:
    ids = [op["id"] for op in registry["ops"]]
    lines = [
        '"""Generated operation IDs and wire schemas. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Literal, TypeAlias",
        "",
        f"REGISTRY_DIGEST = {registry['registry_digest']!r}",
        f"API_VERSION = {registry['api_version']!r}",
        "OperationId: TypeAlias = Literal[",
    ]
    lines.extend(f"    {op_id!r}," for op_id in ids)
    lines.extend(["]", "", "OP_SCHEMAS = {"])
    lines.extend(
        f"    {op['id']!r}: { {'params': op['params'], 'result': op['result']}!r},"
        for op in registry["ops"]
    )
    return ("\n".join([*lines, "}", ""])).encode()


def _client_invoker() -> bytes:
    return b'''"""Generic typed GraphOS operation invoker. Generated; do not edit."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ._generated_models import OperationId


class Transport(Protocol):
    async def invoke(self, op_id: str, params: Mapping[str, Any]) -> Mapping[str, Any]: ...


async def invoke(
    transport: Transport, op_id: OperationId, params: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Use one transport entrypoint for every declared operation."""
    return await transport.invoke(op_id, params)
'''


def _ts_client(registry: dict[str, Any]) -> bytes:
    """Emit the digest-bound generic client consumed by agent-webui."""
    ids = " | ".join(json.dumps(op["id"]) for op in registry["ops"])
    lines = [
        "// Generated by GraphOS scripts/gen_api.py. Do not edit.",
        f"export const registryDigest = {json.dumps(registry['registry_digest'])} as const;",
        f"export const apiVersion = {json.dumps(str(registry['api_version']))} as const;",
        f"export type OperationId = {ids};",
        "export type OperationEnvelope<T = unknown> =",
        "  | { ok: true; result: T; meta: Record<string, unknown> }",
        "  | { ok: false; error: { code: string; source: string; message: string; retryable: boolean }; meta: Record<string, unknown> };",
        "export type GraphOsTransport = (opId: OperationId, params: Record<string, unknown>) => Promise<OperationEnvelope>;",
        "export function invoke<Op extends OperationId>(",
        "  transport: GraphOsTransport, opId: Op, params: Record<string, unknown>",
        "): Promise<OperationEnvelope> {",
        "  return transport(opId, params);",
        "}",
        "",
    ]
    return "\n".join(lines).encode()


def generate(registry: dict[str, Any], errors_path: Path) -> dict[Path, bytes]:
    if not registry.get("ops"):
        raise ValueError("refusing to generate an empty API registry")
    return {
        GENERATED / "registry.json": _json_bytes(registry),
        GENERATED / "descriptors.json": _json_bytes(_descriptors(registry)),
        GENERATED / "engine_errors.py": _engine_errors(errors_path),
        GENERATED / "__init__.py": b'"""Generated GraphOS API contract artifacts."""\n',
        ROOT / "docs/api/openapi.json": _json_bytes(_openapi(registry)),
        ROOT
        / "graph_os/client/__init__.py": b'"""Generated GraphOS client surface."""\n',
        ROOT / "graph_os/client/_generated_models.py": _client_models(registry),
        ROOT / "graph_os/client/invoke.py": _client_invoker(),
        ROOT / "docs/api/generated.ts": _ts_client(registry),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--eg-errors", type=Path, required=True)
    parser.add_argument("--registry-module", default="graph_os.api.ops")
    args = parser.parse_args()
    try:
        outputs = generate(_registry(args.registry_module), args.eg_errors)
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
        print(f"gen_api: {exc}", file=sys.stderr)
        return 2
    drift = [
        path
        for path, content in outputs.items()
        if not path.exists() or path.read_bytes() != content
    ]
    if args.check:
        for path in drift:
            print(f"out of date: {path.relative_to(ROOT)}", file=sys.stderr)
        return int(bool(drift))
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
