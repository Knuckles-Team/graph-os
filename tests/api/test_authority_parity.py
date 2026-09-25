"""G6 fixture oracle for declared operations and the real narrowing PDP.

The live transport matrix is tracked in authority_matrix.yaml until the T5
composition root can supply one verified caller to every surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
        engine_claims={"principal": f"fixture:{name}", "tenant": "fixture-tenant"},
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
    from graph_os.api.mcp.discovery import find_visible
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


def test_fixture_names_only_live_declared_operations_and_explicit_pending_binders() -> (
    None
):
    assert set(MATRIX["principals"])
    assert set(MATRIX["ops"]) <= {op.id for op in REGISTRY}
    for cases in MATRIX["ops"].values():
        assert set(cases) == set(MATRIX["principals"])
    assert MATRIX["pending_live_binders"]


async def test_pinned_eg_contract_composes_every_declared_op_without_scope_injection() -> (
    None
):
    """All current operations must be discoverable through one authority index."""
    from graph_os.api.mcp.discovery import visible_ops
    from graph_os.api.ops import get_registry
    from graph_os.api.policy import op_resource

    registry = get_registry()
    assert len(registry) >= 460
    for principal in MATRIX["principals"]:
        caller = _caller(principal)
        gate = _gate()
        operations = tuple(registry)
        decisions = await gate.visible([op_resource(op) for op in operations], caller)
        allowed = {
            op.id
            for op, decision in zip(operations, decisions, strict=True)
            if decision
        }
        for surface in (Surface.MCP, Surface.HTTP, Surface.A2A):
            projected = {
                op.id
                for op in registry.find(
                    caller,
                    surface=surface,
                    policy=lambda op, _, ids=allowed: op.id in ids,
                )
            }
            expected = {
                op.id
                for op in operations
                if op.id in allowed and surface in op.surfaces
            }
            assert projected == expected
            if surface is Surface.MCP:
                actual_mcp = {op.id for op in await visible_ops(registry, caller, gate)}
                assert actual_mcp == expected
