"""EG wheel contract coverage and authority derivation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_os.api.registry.eg_binding import EgContractError, load_eg_bindings
from graph_os.api.registry.spec import (
    AuditClass,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


def _fixture(root: Path) -> tuple[Path, Path]:
    contract = root / "contract"
    schemas = contract / "schemas"
    schemas.mkdir(parents=True)
    (contract / "scopes.json").write_text(
        json.dumps(
            {
                "scopes": [
                    {"scope": "query:read", "class": "domain"},
                    {"scope": "admin:cluster", "class": "admin"},
                    {"scope": "service:control", "class": "service-only"},
                ]
            }
        )
    )
    for file_name in ("method.request.json", "result.query.json"):
        (schemas / file_name).write_text(
            json.dumps({"methods": {"Read": {}, "Admin": {}, "Service": {}}})
        )
    methods = []
    for method_id, scope, mutates, replay in (
        ("Read", "query:read", False, "NotReplayable"),
        ("Admin", "admin:cluster", True, "OperationIdentity"),
        ("Service", "service:control", True, "NonceOnly"),
    ):
        methods.append(
            {
                "id": method_id,
                "domain": "query",
                "is_wire_callable": True,
                "policy": {
                    "authz_action": scope,
                    "mutates": mutates,
                    "idempotent": True,
                },
                "replay_class": replay,
                "stability": "stable",
                "note": method_id,
                "request_schema": {
                    "schema": f"contract/schemas/method.request.json#/methods/{method_id}"
                },
                "result_schema": {
                    "schema": f"contract/schemas/result.query.json#/methods/{method_id}"
                },
            }
        )
    (contract / "methods.json").write_text(json.dumps({"methods": methods}))
    exclusions = root / "exclusions.yaml"
    exclusions.write_text("exclusions: []\n")
    return contract, exclusions


def _curated(method: str, op_id: str) -> OpSpec:
    scope = "admin:cluster" if method == "Admin" else "query:read"
    return OpSpec(
        id=op_id,
        verb=Verb.ASK,
        summary="Curated operation",
        examples=("curated",),
        params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/Read"),
        result=EgSchemaRef(path="contract/schemas/result.query.json#/methods/Read"),
        binding=EgMethod(service=method, op=method),
        scopes=frozenset({scope}),
    )


@pytest.mark.spec(
    "GRAPHOS-FLEET-R011",
    "GRAPHOS-FLEET-R012",
    "GRAPHOS-OPS-R007",
    "GRAPHOS-OPS-R008",
    "GRAPHOS-OPS-R010",
    "GRAPHOS-OPS-R025",
    "GRAPHOS-OPS-R028",
    "GRAPHOS-OPS-R034",
)
def test_generates_exact_caller_bound_ops(tmp_path: Path) -> None:
    contract, exclusions = _fixture(tmp_path)
    ops = load_eg_bindings(contract_root=contract, exclusions_path=exclusions)
    assert [op.id for op in ops] == [
        "eg.query.Admin",
        "eg.query.Read",
        "eg.query.Service",
    ]
    assert all(
        op.binding
        == EgMethod(service=op.id.rsplit(".", 1)[1], op=op.id.rsplit(".", 1)[1])
        for op in ops
    )
    admin, read, service = ops
    assert (
        admin.effect is Effect.ADMIN and admin.idempotency is Idempotency.KEY_REQUIRED
    )
    assert admin.audit is AuditClass.EVENT
    assert read.effect is Effect.READ and read.idempotency is Idempotency.NATURAL
    assert service.principals.value == "service_only"
    assert all(op.executor.value == "caller" for op in ops)


@pytest.mark.spec(
    "GRAPHOS-FLEET-R011",
    "GRAPHOS-FLEET-R012",
    "GRAPHOS-OPS-R007",
    "GRAPHOS-OPS-R008",
    "GRAPHOS-OPS-R010",
    "GRAPHOS-OPS-R025",
    "GRAPHOS-OPS-R028",
    "GRAPHOS-OPS-R034",
)
def test_curated_binding_suppresses_generated_method(tmp_path: Path) -> None:
    contract, exclusions = _fixture(tmp_path)
    ops = load_eg_bindings(
        contract_root=contract,
        exclusions_path=exclusions,
        curated_ops=iter((_curated("Read", "query.read"),)),
    )
    assert {op.id for op in ops} == {"eg.query.Admin", "eg.query.Service"}


@pytest.mark.spec(
    "GRAPHOS-FLEET-R011",
    "GRAPHOS-FLEET-R012",
    "GRAPHOS-OPS-R007",
    "GRAPHOS-OPS-R008",
    "GRAPHOS-OPS-R010",
    "GRAPHOS-OPS-R025",
    "GRAPHOS-OPS-R028",
    "GRAPHOS-OPS-R034",
)
def test_curated_binding_cannot_weaken_engine_scope(tmp_path: Path) -> None:
    contract, exclusions = _fixture(tmp_path)
    unsafe = _curated("Admin", "query.admin").model_copy(
        update={"scopes": frozenset({"query:read"})}
    )
    with pytest.raises(EgContractError, match="curated EG authority differs"):
        load_eg_bindings(
            contract_root=contract, exclusions_path=exclusions, curated_ops=(unsafe,)
        )


def test_exclusion_needs_current_method_and_valid_reason(tmp_path: Path) -> None:
    contract, exclusions = _fixture(tmp_path)
    exclusions.write_text("exclusions:\n  - method: Read\n    reason: service-only\n")
    assert "eg.query.Read" not in {
        op.id
        for op in load_eg_bindings(contract_root=contract, exclusions_path=exclusions)
    }
    exclusions.write_text(
        "exclusions:\n  - method: Gone\n    reason: engine-internal\n"
    )
    with pytest.raises(EgContractError, match="stale EG exclusion"):
        load_eg_bindings(contract_root=contract, exclusions_path=exclusions)
    exclusions.write_text("exclusions:\n  - method: Read\n    reason: arbitrary\n")
    with pytest.raises(EgContractError, match="invalid exclusion reason"):
        load_eg_bindings(contract_root=contract, exclusions_path=exclusions)


def test_superseded_exclusion_requires_registered_op(tmp_path: Path) -> None:
    contract, exclusions = _fixture(tmp_path)
    exclusions.write_text(
        "exclusions:\n  - method: Read\n    reason: superseded-by:query.read\n"
    )
    with pytest.raises(EgContractError, match="absent superseding op"):
        load_eg_bindings(contract_root=contract, exclusions_path=exclusions)
    load_eg_bindings(
        contract_root=contract,
        exclusions_path=exclusions,
        curated_ops=(_curated("Admin", "query.read"),),
    )


def test_missing_wire_flag_or_scope_fails_closed(tmp_path: Path) -> None:
    contract, exclusions = _fixture(tmp_path)
    path = contract / "methods.json"
    document = json.loads(path.read_text())
    del document["methods"][0]["is_wire_callable"]
    path.write_text(json.dumps(document))
    with pytest.raises(EgContractError, match="missing is_wire_callable"):
        load_eg_bindings(contract_root=contract, exclusions_path=exclusions)
    document["methods"][0]["is_wire_callable"] = True
    document["methods"][0]["policy"]["authz_action"] = "unknown:scope"
    path.write_text(json.dumps(document))
    with pytest.raises(EgContractError, match="unregistered EG scope"):
        load_eg_bindings(contract_root=contract, exclusions_path=exclusions)


def test_missing_schema_pointer_fails_closed(tmp_path: Path) -> None:
    contract, exclusions = _fixture(tmp_path)
    path = contract / "methods.json"
    document = json.loads(path.read_text())
    document["methods"][0]["request_schema"]["schema"] += "Missing"
    path.write_text(json.dumps(document))
    with pytest.raises(EgContractError, match="missing schema pointer"):
        load_eg_bindings(contract_root=contract, exclusions_path=exclusions)
