"""EH-407: the console grant route decides approvals as the operator console.

Only this route may decide a console-only approval (a guardrail loosening);
the agent governance tool decides as an agent tool and is refused by AU.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent_utilities.orchestration.approval import ApprovalSurface
from starlette.applications import Starlette
from starlette.testclient import TestClient

from graph_os.gateway.fleet import fleet_grant_approval


def test_the_console_grant_route_decides_as_the_operator_console(
    monkeypatch: Any,
) -> None:
    seen: list[Any] = []

    def decide(engine: Any, job_id: str, decision: str, surface: Any) -> Any:
        seen.append(surface)
        return {"approval_id": job_id, "decision": "approved"}

    monkeypatch.setattr(
        "agent_utilities.orchestration.approval.decide_action_approval", decide
    )
    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application",
        lambda: SimpleNamespace(engine=lambda: object()),
    )
    app = Starlette()
    app.add_route("/fleet/approvals/grant", fleet_grant_approval, methods=["POST"])
    response = TestClient(app).post(
        "/fleet/approvals/grant",
        json={"job_id": "action_approval:1", "decision": "approved"},
    )
    assert response.status_code == 200
    assert seen == [ApprovalSurface.OPERATOR_CONSOLE]
