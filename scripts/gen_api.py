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
import importlib.resources
import json
import subprocess
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
            or not 300 <= status <= 599
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
        f"SOURCE_SHA256 = {json.dumps(hashlib.sha256(raw).hexdigest())}",
        "ENGINE_ERRORS: dict[str, tuple[int, bool]] = {",
    ]
    lines.extend(f"    {json.dumps(code)}: {rows[code]!r}," for code in sorted(rows))
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


def _schema(
    value: dict[str, Any], components: dict[str, Any], contract_root: Path | None
) -> dict[str, Any]:
    if "schema" in value:
        return value["schema"]
    if "eg_schema" in value:
        reference = value["eg_schema"]
        filename, separator, pointer = reference.partition("#")
        if not separator or not filename.startswith("contract/schemas/"):
            raise ValueError(f"invalid EG schema reference: {reference!r}")
        relative = Path(filename.removeprefix("contract/"))
        if ".." in relative.parts or contract_root is None:
            raise ValueError(f"EG schema unavailable: {reference!r}")
        name = "Eg" + "".join(part.title() for part in relative.stem.split("."))
        if name not in components:
            document = json.loads((contract_root / relative).read_bytes())
            components[name] = _rewrite_schema_refs(document, name)
        return {"$ref": f"#/components/schemas/{name}{pointer}"}
    raise ValueError(f"unknown schema reference: {value!r}")


def _rewrite_schema_refs(value: Any, name: str) -> Any:
    if isinstance(value, list):
        return [_rewrite_schema_refs(item, name) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                f"#/components/schemas/{name}{item[1:]}"
                if key == "$ref" and isinstance(item, str) and item.startswith("#/")
                else _rewrite_schema_refs(item, name)
            )
            for key, item in value.items()
        }
    return value


def _openapi(
    registry: dict[str, Any], contract_root: Path | None = None
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    components: dict[str, Any] = {}
    if contract_root is None and any(
        "eg_schema" in op[field]
        for op in registry["ops"]
        for field in ("params", "result")
    ):
        contract_root = Path(
            str(importlib.resources.files("epistemic_graph") / "contract")
        )
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
                "content": {
                    "application/json": {
                        "schema": _schema(op["params"], components, contract_root)
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Operation result",
                    "content": {
                        "application/json": {
                            "schema": _schema(op["result"], components, contract_root)
                        }
                    },
                },
                "default": {"description": "GraphOS error envelope"},
            },
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "GraphOS API", "version": str(registry["api_version"])},
        "x-registry-digest": registry["registry_digest"],
        "paths": paths,
        "components": {"schemas": components},
    }


def _client_models(registry: dict[str, Any]) -> bytes:
    ids = [op["id"] for op in registry["ops"]]
    lines = [
        '"""Generated operation IDs and schema lookup. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "import importlib.resources",
        "import json",
        "from functools import lru_cache",
        "from typing import Literal",
        "",
        f"REGISTRY_DIGEST = {json.dumps(registry['registry_digest'])}",
        f"API_VERSION = {json.dumps(str(registry['api_version']))}",
        "type OperationId = Literal[",
    ]
    lines.extend(f"    {json.dumps(op_id)}," for op_id in ids)
    lines.extend(
        [
            "]",
            "",
            "",
            "@lru_cache(maxsize=1)",
            "def _registry() -> dict:",
            '    source = importlib.resources.files("graph_os.api.generated") / "registry.json"',
            "    registry = json.loads(source.read_bytes())",
            '    if registry["registry_digest"] != REGISTRY_DIGEST:',
            '        raise RuntimeError("generated client registry digest mismatch")',
            "    return registry",
            "",
            "",
            "def schema_for(op_id: OperationId) -> dict:",
            '    for op in _registry()["ops"]:',
            '        if op["id"] == op_id:',
            '            return {"params": op["params"], "result": op["result"]}',
            "    raise KeyError(op_id)",
            "",
        ]
    )
    return "\n".join(lines).encode()


def _client_invoker() -> bytes:
    return b'''"""Generic typed GraphOS operation invoker. Generated; do not edit."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ._generated_models import OperationId


class Transport(Protocol):
    async def invoke(
        self, op_id: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


async def invoke(
    transport: Transport, op_id: OperationId, params: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Use one transport entrypoint for every declared operation."""
    return await transport.invoke(op_id, params)
'''


def _ts_client(registry: dict[str, Any]) -> bytes:
    """Emit the digest-bound generic client consumed by graph-os-webui."""
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


def _breaking_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Identify contract changes that existing stable callers cannot absorb."""
    current = {op["id"]: op for op in after["ops"]}
    changes: list[str] = []
    for previous in before["ops"]:
        if previous.get("stability") != "stable":
            continue
        op_id = previous["id"]
        updated = current.get(op_id)
        if updated is None:
            changes.append(f"{op_id}: removed")
            continue
        old_params = previous["params"].get("schema", {})
        new_params = updated["params"].get("schema", {})
        required = set(new_params.get("required", [])) - set(
            old_params.get("required", [])
        )
        if required:
            changes.append(f"{op_id}: new required params {sorted(required)}")
        old_props = old_params.get("properties", {})
        new_props = new_params.get("properties", {})
        removed_params = set(old_props) - set(new_props)
        if removed_params:
            changes.append(f"{op_id}: removed params {sorted(removed_params)}")
        for name in old_props.keys() & new_props.keys():
            old_enum = old_props[name].get("enum")
            new_enum = new_props[name].get("enum")
            if old_enum and new_enum and not set(old_enum) <= set(new_enum):
                changes.append(f"{op_id}: narrowed param enum {name}")
            old_type = old_props[name].get("type")
            new_type = new_props[name].get("type")
            if old_type != new_type and (old_type, new_type) != (
                "integer",
                "number",
            ):
                changes.append(f"{op_id}: changed param type {name}")
        old_result = previous["result"].get("schema", {})
        new_result = updated["result"].get("schema", {})
        removed = set(old_result.get("properties", {})) - set(
            new_result.get("properties", {})
        )
        if removed:
            changes.append(f"{op_id}: removed result fields {sorted(removed)}")
    return changes


def _check_compat(registry: dict[str, Any], base_ref: str) -> int:
    subprocess.run(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    baseline = subprocess.run(
        ["git", "show", f"{base_ref}:graph_os/api/generated/registry.json"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if baseline.returncode:
        print("gen_api: no baseline generated registry at base ref (first release)")
        return 0
    previous = json.loads(baseline.stdout)
    changes = _breaking_changes(previous, registry)
    if not changes:
        return 0
    old_major = int(str(previous["api_version"]).split(".", 1)[0])
    new_major = int(str(registry["api_version"]).split(".", 1)[0])
    changelog = ROOT / "docs/api/CHANGELOG.md"
    text = changelog.read_text() if changelog.exists() else ""
    if new_major > old_major and all(row.split(":", 1)[0] in text for row in changes):
        return 0
    for row in changes:
        print(f"breaking API change: {row}", file=sys.stderr)
    print(
        "gen_api: stable breaking changes require a major bump and changelog entry",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--engine-errors-only", action="store_true")
    parser.add_argument("--check-compat", metavar="BASE_REF")
    parser.add_argument(
        "--eg-errors",
        type=Path,
        help="EG error catalog; defaults to the pinned epistemic_graph wheel",
    )
    parser.add_argument("--registry-module", default="graph_os.api.ops")
    args = parser.parse_args()
    try:
        registry = None if args.engine_errors_only else _registry(args.registry_module)
        if args.check_compat:
            if registry is None:
                raise ValueError("--check-compat cannot use --engine-errors-only")
            return _check_compat(registry, args.check_compat)
        errors_path = args.eg_errors
        if errors_path is None:
            errors_path = Path(
                str(
                    importlib.resources.files("epistemic_graph")
                    / "contract/errors.json"
                )
            )
        if args.engine_errors_only:
            outputs = {
                GENERATED / "engine_errors.py": _engine_errors(errors_path),
                GENERATED
                / "__init__.py": b'"""Generated GraphOS API contract artifacts."""\n',
            }
        else:
            assert registry is not None
            outputs = generate(registry, errors_path)
    except (
        AttributeError,
        ImportError,
        OSError,
        subprocess.CalledProcessError,
        TypeError,
        ValueError,
    ) as exc:
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
