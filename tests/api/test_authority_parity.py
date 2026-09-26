"""G6 fixture oracle for declared operations and the real narrowing PDP.

The live transport matrix is tracked in authority_matrix.yaml until the T5
composition root can supply one verified caller to every surface.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from graph_os.api.invoke.steps import VerifiedCaller, principal_rule, require_scopes
from graph_os.api.ops import ops
from graph_os.api.policy import PolicyGate, fleet_resource
from graph_os.api.policy.pdp_remote import EmbeddedPolicy, load_policy_file
from graph_os.api.registry import Registry, Surface

ROOT = Path(__file__).parent
MATRIX = json.loads((ROOT / "authority_matrix.yaml").read_text())
POLICY = load_policy_file(str(ROOT / "eunomia_fixtures/authority_policy.yaml"))
REGISTRY = Registry(ops.specs())


def _caller(name: str) -> VerifiedCaller:
    fixture = MATRIX["principals"][name]
    return VerifiedCaller(
        principal=f"fixture:{name}",
        tenant="fixture-tenant",
        effective_scopes=frozenset(fixture["scopes"]),
        engine_claims={
            "principal": f"fixture:{name}",
            "tenant": "fixture-tenant",
            "scopes": fixture["scopes"],
            "delegation": fixture.get("delegated", False),
        },
        principal_kind=fixture["kind"],
        delegated=fixture.get("delegated", False),
        policy_revision="fixture-rev-1",
    )


def _gate() -> PolicyGate:
    return PolicyGate("embedded", EmbeddedPolicy([POLICY]))


@pytest.mark.parametrize(
    ("op_id", "principal"),
    [
        (op_id, principal)
        for op_id, cases in MATRIX["ops"].items()
        for principal in cases
    ],
)
async def test_authority_matrix_uses_real_scope_principal_and_eunomia_checks(
    op_id: str, principal: str
) -> None:
    op = REGISTRY[op_id]
    caller = _caller(principal)
    expected = MATRIX["ops"][op_id][principal]
    rule = principal_rule(op, caller)
    scope = require_scopes(op, caller)
    policy = await _gate().check_op(op, caller)
    code = (
        rule.code
        if rule
        else scope.code
        if scope
        else "OK"
        if policy
        else "POLICY_DENIED"
    )
    assert code == expected
    for surface in MATRIX["surface_projections"]:
        assert Surface(surface) in op.surfaces
        # Registry discovery and invocation use the same declared rule and
        # exact scope. Eunomia may only narrow their common candidate set.
        discovered = REGISTRY.find(
            caller,
            surface=Surface(surface),
            policy=lambda item, _: item.id == op_id and policy,
        )
        assert (op in discovered) is (expected == "OK")


@pytest.mark.parametrize(
    "case",
    MATRIX["surface_invocation_cases"],
    ids=lambda case: f"{case['op']}-{case['principal']}",
)
async def test_same_authority_code_through_mcp_http_and_a2a(
    case: dict[str, str],
) -> None:
    """Exercise three transport adapters over one invoke bundle and real PDP."""
    from fastapi import HTTPException

    from graph_os.a2a.application import create_a2a_application
    from graph_os.a2a.op_invoke import OperationProjection
    from graph_os.a2a.service import A2AService
    from graph_os.api.http.app import create_api_application
    from graph_os.api.invoke import InvokeServices
    from graph_os.api.mcp.verbs import MCPProjection, dispatch_verb

    op = REGISTRY[case["op"]]
    caller = _caller(case["principal"])
    gate = _gate()
    registry = Registry((op,))

    class Runtime:
        service_scopes = frozenset()

        @asynccontextmanager
        async def as_caller(self, selected: VerifiedCaller) -> Any:
            yield selected

        async def dispatch(self, selected_op: Any, params: Any, context: Any) -> Any:
            return {"op": selected_op.id}

    async def audit_write(*_args: Any) -> None:
        pass

    services = InvokeServices(
        registry=registry,
        runtime=Runtime(),
        plans=SimpleNamespace(),
        policy_mode="on",
        policy_check=gate.check_op,
        audit_write=audit_write,
        schema_validate=lambda ref, params: params,
    )
    projection = MCPProjection(
        registry=registry,
        services=services,
        resolver=SimpleNamespace(scope_ref=lambda *args, **kwargs: "fixture"),
        caller_for_request=lambda: caller,
        policy_gate=gate,
    )
    mcp = await dispatch_verb(op.verb.value, projection, op=op.id, params={})
    mcp_code = "OK" if mcp["ok"] else mcp["error"]["code"]

    class HTTPAuthenticator:
        async def authenticate(self, request: Any) -> VerifiedCaller:
            return caller

        def is_console_request(self, request: Any, selected: VerifiedCaller) -> bool:
            return False

    http_app = create_api_application(
        services=services,
        visibility=gate.check_op,
        authenticator=HTTPAuthenticator(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_app), base_url="http://fixture.test"
    ) as client:
        http_response = await client.post(f"/ops/{op.id}", json={})
    http_body = http_response.json()
    http_code = "OK" if http_body["ok"] else http_body["error"]["code"]

    class A2AAuthenticator:
        async def authenticate(self, request: Any, *, scope: str) -> None:
            if request.headers.get("Authorization") != "Bearer verified":
                raise HTTPException(status_code=401)
            assert scope == ""

    a2a_app = create_a2a_application(
        service=A2AService(authority=SimpleNamespace(), router=SimpleNamespace()),
        authenticator=A2AAuthenticator(),
        operation_projection=OperationProjection(services, caller=lambda: caller),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=a2a_app), base_url="http://fixture.test"
    ) as client:
        a2a_response = await client.post(
            "/a2a",
            headers={"Authorization": "Bearer verified"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "graphos.op/invoke",
                "params": {"op": op.id, "params": {}},
            },
        )
    a2a_body = a2a_response.json()
    a2a_code = "OK" if "result" in a2a_body else a2a_body["error"]["data"]["code"]

    assert (mcp_code, http_code, a2a_code) == (
        MATRIX["ops"][op.id][case["principal"]],
    ) * 3


async def test_mcp_discovery_matches_the_authority_fixture() -> None:
    from graph_os.api.mcp.discovery import visible_ops

    for principal in MATRIX["principals"]:
        caller = _caller(principal)
        actual = {op.id for op in await visible_ops(REGISTRY, caller, _gate())}
        expected = {
            op_id for op_id, cases in MATRIX["ops"].items() if cases[principal] == "OK"
        }
        assert actual & set(MATRIX["ops"]) == expected


async def test_mcp_and_http_registry_discovery_agree_for_fixture_callers() -> None:
    from graph_os.api.http.app import create_api_application
    from graph_os.api.mcp.discovery import visible_ops

    class FixtureAuthenticator:
        def __init__(self, caller: VerifiedCaller) -> None:
            self.caller = caller

        async def authenticate(self, request: object) -> VerifiedCaller:
            return self.caller

        def is_console_request(self, request: object, caller: VerifiedCaller) -> bool:
            return False

    for principal in MATRIX["principals"]:
        caller = _caller(principal)
        gate = _gate()
        mcp = {op.id for op in await visible_ops(REGISTRY, caller, gate)}
        app = create_api_application(
            services=SimpleNamespace(registry=REGISTRY),
            visibility=gate.check_op,
            authenticator=FixtureAuthenticator(caller),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture.test"
        ) as client:
            response = await client.get("/registry")
        assert response.status_code == 200
        http = {op["id"] for op in response.json()["ops"]}
        assert mcp == http


async def test_fleet_catalog_and_mcp_find_share_policy_filtered_item_set() -> None:
    from fastapi import HTTPException

    from graph_os.a2a.application import create_a2a_application
    from graph_os.a2a.op_invoke import OperationProjection
    from graph_os.a2a.service import A2AService
    from graph_os.api.http.app import create_api_application
    from graph_os.api.invoke import InvokeServices
    from graph_os.api.mcp.discovery import find_visible
    from graph_os.api.ops.fleet import handle_fleet_operation, operations
    from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog

    items = tuple(
        CatalogItem(
            id=item_id,
            kind="tool",
            name=item_id.rsplit("/", 1)[-1],
            server="sample",
            required_scopes=frozenset(case["scopes"]),
        )
        for item_id, case in MATRIX["fleet_items"].items()
    )

    async def source() -> tuple[CatalogItem, ...]:
        return items

    class Gateway:
        def __init__(self, catalog: FleetCatalog) -> None:
            self.catalog = catalog

        async def search(self, caller: VerifiedCaller, **params: Any) -> Any:
            return await self.catalog.search(caller, **params)

    class Runtime:
        service_scopes = frozenset()

        def __init__(self, gateway: Gateway) -> None:
            self.bindings = {"fleet_gateway": gateway}

        @asynccontextmanager
        async def as_caller(self, caller: VerifiedCaller) -> Any:
            yield caller

        async def dispatch(self, op: Any, params: Any, context: Any) -> Any:
            return await handle_fleet_operation(context, params, op)

    class HTTPAuthenticator:
        def __init__(self, caller: VerifiedCaller) -> None:
            self.caller = caller

        async def authenticate(self, request: Any) -> VerifiedCaller:
            return self.caller

        def is_console_request(self, request: Any, caller: VerifiedCaller) -> bool:
            return False

    class A2AAuthenticator:
        async def authenticate(self, request: Any, *, scope: str) -> None:
            if request.headers.get("Authorization") != "Bearer verified":
                raise HTTPException(status_code=401)
            assert scope == ""

    async def audit_write(*_args: Any) -> None:
        pass

    op = next(op for op in operations() if op.id == "fleet.catalog.search")
    assert op.http is not None and op.http.path == "/api/v1/fleet/catalog"
    registry = Registry((op,))

    for principal in MATRIX["principals"]:
        caller = _caller(principal)
        gate = _gate()

        async def visible(
            item: CatalogItem, selected: VerifiedCaller, active_gate: PolicyGate = gate
        ) -> bool:
            resource = fleet_resource(
                "tool",
                f"{item.server}/{item.name}",
                required_scopes=item.required_scopes,
            )
            return (await active_gate.visible([resource], selected))[0]

        catalog = FleetCatalog((source,), visible)

        async def fleet_search(
            active_catalog: FleetCatalog = catalog, **kwargs: object
        ) -> list[dict[str, object]]:
            page = await active_catalog.search(kwargs["caller"], browse=True)
            return page["items"]

        page = await catalog.search(caller, browse=True)
        merged = await find_visible(
            registry=REGISTRY,
            caller=caller,
            policy_gate=gate,
            resolver=None,
            fleet_search=fleet_search,
            params={"limit": 100},
        )
        catalog_ids = {item["id"] for item in page["items"]}
        find_ids = {item["id"] for item in merged["items"] if item["kind"] == "fleet"}
        expected = {
            item_id
            for item_id, case in MATRIX["fleet_items"].items()
            if principal in case["allow"]
        }
        assert catalog_ids == find_ids == expected

        services = InvokeServices(
            registry=registry,
            runtime=Runtime(Gateway(catalog)),
            plans=SimpleNamespace(),
            policy_mode="on",
            policy_check=gate.check_op,
            audit_write=audit_write,
        )
        http_app = create_api_application(
            services=services,
            visibility=gate.check_op,
            authenticator=HTTPAuthenticator(caller),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http_app), base_url="http://fixture.test"
        ) as client:
            http_response = await client.get("/fleet/catalog", params={"browse": True})
        assert http_response.status_code == 200
        http_ids = {
            item["id"] for item in http_response.json()["result"]["value"]["items"]
        }

        a2a_app = create_a2a_application(
            service=A2AService(authority=SimpleNamespace(), router=SimpleNamespace()),
            authenticator=A2AAuthenticator(),
            operation_projection=OperationProjection(
                services, caller=lambda selected=caller: selected
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=a2a_app), base_url="http://fixture.test"
        ) as client:
            a2a_response = await client.post(
                "/a2a",
                headers={"Authorization": "Bearer verified"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "graphos.op/invoke",
                    "params": {
                        "op": "fleet.catalog.search",
                        "params": {"browse": True},
                    },
                },
            )
        assert a2a_response.status_code == 200
        a2a_ids = {
            item["id"] for item in a2a_response.json()["result"]["value"]["items"]
        }
        assert http_ids == a2a_ids == expected


def test_fixture_names_only_live_declared_operations_and_explicit_pending_binders() -> (
    None
):
    assert set(MATRIX["principals"])
    assert set(MATRIX["ops"]) <= {op.id for op in REGISTRY}
    for cases in MATRIX["ops"].values():
        assert set(cases) == set(MATRIX["principals"])
    assert MATRIX["pending_live_binders"]


async def test_generated_registry_discovery_agrees_across_all_three_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project every generated op from one embedded PDP decision per caller."""
    from graph_os.a2a.mcp import _operation_projection
    from graph_os.api.http.app import create_api_application
    from graph_os.api.mcp.discovery import visible_ops
    from graph_os.api.ops import get_registry
    from graph_os.api.policy import op_resource
    from graph_os.mcp_server import runtime

    registry = get_registry()
    operations = tuple(registry)
    assert operations

    class Authenticator:
        caller: VerifiedCaller

        async def authenticate(self, request: Any) -> VerifiedCaller:
            return self.caller

        def is_console_request(self, request: Any, caller: VerifiedCaller) -> bool:
            return False

    class Visibility:
        allowed: set[str]

        async def __call__(self, op: Any, caller: VerifiedCaller) -> bool:
            return op.id in self.allowed

    auth = Authenticator()
    visibility = Visibility()
    app = create_api_application(
        services=SimpleNamespace(registry=registry),
        visibility=visibility,
        authenticator=auth,
    )

    for principal in MATRIX["principals"]:
        caller = _caller(principal)
        gate = _gate()
        decisions = await gate.visible([op_resource(op) for op in operations], caller)
        assert len(decisions) == len(operations)
        allowed = {
            op.id
            for op, decision in zip(operations, decisions, strict=True)
            if decision is True
        }

        def expected(
            surface: Surface,
            active_caller: VerifiedCaller = caller,
            active_allowed: set[str] = allowed,
        ) -> set[str]:
            return {
                op.id
                for op in registry.find(
                    active_caller,
                    surface=surface,
                    policy=lambda item, selected: item.id in active_allowed,
                )
            }

        assert {op.id for op in await visible_ops(registry, caller, gate)} == expected(
            Surface.MCP
        )
        auth.caller = caller
        visibility.allowed = allowed
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture.test"
        ) as client:
            response = await client.get("/registry")
        assert response.status_code == 200
        assert {op["id"] for op in response.json()["ops"]} == expected(Surface.HTTP)

        bound = SimpleNamespace(
            registry=registry,
            services=SimpleNamespace(registry=registry),
            policy_gate=gate,
            caller_for_request=lambda selected=caller: selected,
        )
        monkeypatch.setattr(runtime, "served_api", lambda active=bound: (active, None))
        assert {op.id for op in await _operation_projection().visible_card_ops()} == (
            expected(Surface.A2A)
        )
