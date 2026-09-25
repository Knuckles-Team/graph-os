"""EH-423: an agent can propose a live order; only a different person with the
exact approval scope, at the console, can approve it -- and the approval is
recorded under that person's own verified identity."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.applications import Starlette

from graph_os.finance.orders import APPROVAL_KIND, APPROVE_SCOPE
from graph_os.gateway.finance_orders import mount_finance_order_routes
from graph_os.mcp_server import runtime
from graph_os.mcp_server.finance import FinanceToolRequest, register_finance_tools
from tests.finance.fakes import FinanceClient, principal_ref, serving
from tests.mcp_server.test_policy_release import _Mcp

PROPOSE = "finance:propose-order"
INTENT = {
    "symbol": "AAPL",
    "side": "buy",
    "qty": 2,
    "order_type": "limit",
    "limit_price": 101.5,
}


def _session(principal: str, *scopes: str, delegation: tuple[str, ...] = ()) -> Any:
    claims = {
        "agent_id": principal,
        "principal": principal,
        "tenant": "tenant-a",
        "delegation": list(delegation),
        "scopes": sorted(scopes),
    }
    return SimpleNamespace(
        tenant="tenant-a",
        scopes=frozenset(scopes),
        engine_verified_context=lambda: claims,
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:
    client = FinanceClient()
    client.consensus.readers.update({"agent-7", "agent-8"})
    state = {"session": _session("agent-7", PROPOSE)}

    @contextlib.contextmanager
    def scope() -> Any:
        yield state["session"]

    monkeypatch.setattr(runtime, "verified_tool_session_scope", scope)
    monkeypatch.setattr(runtime, "graph_client", lambda tenant: client)
    monkeypatch.setattr(
        "agent_utilities.api.session.resolve_session",
        lambda required_scope=None: state["session"],
    )
    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application",
        lambda: SimpleNamespace(graph_client=lambda tenant: client),
    )
    mcp = _Mcp()
    prior = runtime.REGISTERED_TOOLS.get("graph_finance")
    register_finance_tools(mcp)

    async def tool(**fields: Any) -> Any:
        return await mcp.tools["graph_finance"](FinanceToolRequest(**fields))

    app = Starlette()
    mount_finance_order_routes(app, prefix="/api")
    with serving(client):
        yield SimpleNamespace(
            tool=tool, client=client, state=state, console=TestClient(app)
        )
    runtime.REGISTERED_TOOLS.pop("graph_finance", None)
    if prior is not None:
        runtime.REGISTERED_TOOLS["graph_finance"] = prior


async def _proposed(world: Any) -> dict[str, Any]:
    world.state["session"] = _session("agent-7", PROPOSE)
    return await world.tool(action="propose_order", intent=INTENT, reason="weekly flip")


def _decide(
    world: Any, verb: str, proposal: dict[str, Any], digest: str | None = None
) -> Any:
    body = {
        "approval_id": proposal["approval_id"],
        "intent_digest": digest or proposal["intent_digest"],
    }
    return world.console.post(f"/api/finance/orders/{verb}", json=body)


async def test_an_agent_proposal_is_a_pending_lease_that_places_nothing(
    world: Any,
) -> None:
    proposal = await _proposed(world)
    lease = world.client.control_leases.rows[proposal["approval_id"]]
    assert (lease["kind"], lease["status"]) == (APPROVAL_KIND, "active")
    assert lease["grant"]["proposer"] == principal_ref("agent-7")
    assert world.client.change_sets == {}
    status = await world.tool(
        action="order_status", approval_id=proposal["approval_id"]
    )
    assert status["status"] == "pending_approval"


def test_the_tool_cannot_approve_and_takes_no_identity() -> None:
    with pytest.raises(ValidationError):
        FinanceToolRequest.model_validate(
            {"action": "approve_order", "approval_id": "x"}
        )
    with pytest.raises(ValidationError):
        FinanceToolRequest.model_validate({"action": "order_status", "actor": "bob"})
    from graph_os.api.ops.finance import specs

    assert "finance.orders.propose" in {op.id for op in specs()}


@pytest.mark.parametrize(
    ("session", "code"),
    [
        (_session("ops-1", "kg:write"), "ORDER_APPROVAL_SCOPE_REQUIRED"),
        (
            _session("ops-1", APPROVE_SCOPE, delegation=("agent-7",)),
            "ORDER_APPROVAL_NOT_DELEGABLE",
        ),
        (_session("agent-7", APPROVE_SCOPE), "ORDER_OWN_PROPOSAL"),
    ],
)
async def test_approval_needs_the_exact_scope_a_person_and_a_second_party(
    world: Any, session: Any, code: str
) -> None:
    proposal = await _proposed(world)
    world.state["session"] = session
    answer = _decide(world, "approve", proposal)
    assert (answer.status_code, answer.json()["code"]) == (403, code)
    assert world.client.change_sets == {}
    assert (
        world.client.control_leases.rows[proposal["approval_id"]]["status"] == "active"
    )


async def test_a_stale_view_is_refused(world: Any) -> None:
    proposal = await _proposed(world)
    world.state["session"] = _session("ops-1", APPROVE_SCOPE)
    answer = _decide(world, "approve", proposal, digest="0" * 64)
    assert answer.json()["code"] == "ORDER_STALE_VIEW"


async def test_approval_records_the_change_set_under_the_approver(world: Any) -> None:
    proposal = await _proposed(world)
    world.state["session"] = _session("ops-1", APPROVE_SCOPE)
    answer = _decide(world, "approve", proposal)
    assert answer.status_code == 200, answer.json()
    change_set = world.client.change_sets[f"finance-order:{proposal['approval_id']}"]
    assert change_set["actor"] == principal_ref("ops-1")
    assert change_set["connector_id"] == "emerald-exchange"
    assert change_set["authorization"]["mode"] == "proposal_approval"
    assert change_set["authorization"]["authorization_ref"] == proposal["approval_id"]
    assert change_set["desired_patch"] == {**INTENT, "qty": 2.0}
    assert (
        world.client.control_leases.rows[proposal["approval_id"]]["status"]
        == "consumed"
    )
    again = _decide(world, "approve", proposal)
    assert again.json()["code"] == "ORDER_NOT_PENDING"


async def test_a_denied_proposal_can_never_be_approved(world: Any) -> None:
    proposal = await _proposed(world)
    world.state["session"] = _session("ops-1", APPROVE_SCOPE)
    assert _decide(world, "deny", proposal).json()["order"]["status"] == "denied"
    assert _decide(world, "approve", proposal).json()["code"] == "ORDER_NOT_PENDING"
    assert world.client.change_sets == {}
