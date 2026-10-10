"""Operation identity and discovery authorization contract."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from graph_os.api.registry import (
    AuditClass,
    Caller,
    Confirm,
    Effect,
    EgSchemaRef,
    Executor,
    OpSpec,
    PrincipalRule,
    Registry,
    Stability,
    SubjectRef,
    SubjectSource,
    Surface,
    Verb,
    canonical_registry,
)
from tests.api._support import identity_disable_op_values


class Input(BaseModel):
    user_id: str


class Output(BaseModel):
    disabled: bool


@dataclass(frozen=True)
class _FakeCaller:
    """A concrete stand-in structurally satisfying ``graph_os.api.registry.Caller``."""

    effective_scopes: frozenset[str]
    principal_kind: str = "human"
    delegated: bool = False


def make_op(op_id: str = "identity.users.disable", **changes: Any) -> OpSpec:
    values = identity_disable_op_values(
        op_id,
        params=Input,
        result=Output,
        principals=PrincipalRule.HUMAN_UNDELEGATED,
    )
    values.update(changes)
    return OpSpec(**values)


def allow_all(_op: OpSpec, _caller: Caller) -> bool:
    return True


def test_registry_digest_is_order_independent_and_covers_contract() -> None:
    first = make_op()
    second = make_op("identity.users.list", effect=Effect.READ)
    left = Registry([first, second])
    right = Registry([second, first])
    assert left.canonical == right.canonical
    assert left.digest == right.digest
    assert left.digest != Registry([first, second], api_version="2").digest
    assert (
        left.digest
        != Registry(
            [first, make_op("identity.users.list", scopes={"identity:read"})]
        ).digest
    )
    assert canonical_registry([first, second]) == left.canonical


def test_eg_schema_content_changes_registry_digest(tmp_path: Path) -> None:
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    source = schemas / "method.request.json"
    doc = {
        "methods": {"Foo": {"$ref": "#/$defs/Value"}},
        "$defs": {"Value": {"type": "string"}},
    }
    source.write_text(json.dumps(doc))
    op = make_op(
        params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/Foo")
    )
    first = Registry([op], contract_root=tmp_path)
    doc["$defs"]["Value"]["type"] = "integer"
    source.write_text(json.dumps(doc))
    second = Registry([op], contract_root=tmp_path)
    assert first.digest != second.digest
    assert first.canonical != second.canonical


def test_registry_rejects_duplicate_wire_identity() -> None:
    with pytest.raises(ValueError, match="duplicate operation id"):
        Registry([make_op(), make_op()])


def test_eg_generated_method_id_preserves_contract_case() -> None:
    assert make_op("eg.query.Uql").id == "eg.query.Uql"


def test_discovery_requires_principal_scopes_policy_and_surface() -> None:
    op = make_op()
    registry = Registry([op])
    authorized = _FakeCaller(frozenset({"identity:admin"}))
    assert registry.find(
        authorized, policy=allow_all, verb=Verb.MANAGE, surface=Surface.MCP
    ) == (op,)
    assert registry.find(authorized, policy=allow_all, surface=Surface.CONSOLE) == ()
    assert registry.find(authorized, policy=lambda _op, _caller: False) == ()
    assert registry.find(_FakeCaller(frozenset()), policy=allow_all) == ()
    assert (
        registry.find(
            _FakeCaller(authorized.effective_scopes, delegated=True), policy=allow_all
        )
        == ()
    )
    assert (
        registry.find(
            _FakeCaller(authorized.effective_scopes, principal_kind="service"),
            policy=allow_all,
        )
        == ()
    )


def test_policy_failure_closes_discovery() -> None:
    def unavailable(_op: OpSpec, _caller: Caller) -> bool:
        raise ConnectionError("policy unavailable")

    assert (
        Registry([make_op()]).find(
            _FakeCaller(frozenset({"identity:admin"})), policy=unavailable
        )
        == ()
    )


def test_service_executor_requires_subject_and_executor_scopes() -> None:
    with pytest.raises(ValidationError, match="executor_scopes and subject"):
        make_op(executor=Executor.SERVICE)
    service = make_op(
        executor=Executor.SERVICE,
        subject=SubjectRef(path="params.user_id"),
        executor_scopes=frozenset({"identity:service"}),
    )
    assert service.subject == SubjectRef(path="params.user_id")


def test_tenant_subject_is_typed_and_cannot_read_a_request_field() -> None:
    tenant = SubjectRef(source=SubjectSource.CALLER_TENANT)
    assert tenant.path is None
    with pytest.raises(ValidationError, match="cannot carry a request path"):
        SubjectRef(source=SubjectSource.CALLER_TENANT, path="params.tenant")
    with pytest.raises(ValidationError, match="non-reserved request path"):
        SubjectRef(path="$caller.tenant")


def test_mutating_operation_must_declare_an_audit_class() -> None:
    with pytest.raises(ValidationError, match="requires an audit class"):
        make_op(audit=AuditClass.NONE)


def test_effect_confirmation_defaults_and_deprecation_metadata() -> None:
    assert make_op().confirm is Confirm.CONSOLE
    assert make_op(effect=Effect.DESTRUCTIVE).confirm is Confirm.PLAN
    assert make_op(effect=Effect.READ).confirm is Confirm.NONE
    with pytest.raises(ValidationError, match="remove_in"):
        make_op(stability=Stability.DEPRECATED)
    assert make_op(stability=Stability.DEPRECATED, remove_in="2.0").remove_in == "2.0"
