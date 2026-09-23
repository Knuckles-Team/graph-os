"""Operator surface for model-policy release pointers (EH-347).

``graph_policy_release`` (MCP) and its action-routed REST twin
``POST /graph/policy/release`` read, promote and roll back the release
pointer of one model-policy family/channel. Authority is the served
surface's usual one: the verified tool session (identity, tenant, audience,
policy revision), the tenant's session-routed EG client under
``use_verified_context``, and — for a move — the capability record's
``promote`` control, whose named scope the verified session must hold. A
move is always an explicit compare-and-swap on the revision/version the
operator last read (``status`` returns both).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_os.a2a.policy_training import policy_records
from graph_os.control_plane.policy_evolution import (
    EgReleasePointerRepository,
    ModelPolicyReleaseService,
    PolicyReleaseMutation,
)
from graph_os.mcp_server import runtime

__all__ = ["PolicyReleaseRequest", "register_policy_release_tools"]

TOOL_NAME = "graph_policy_release"
Channel = Literal["canary", "stable"]


def _service(client: Any) -> ModelPolicyReleaseService:
    return ModelPolicyReleaseService(
        policy_records(client), EgReleasePointerRepository(client)
    )


class PolicyReleaseRequest(BaseModel):
    """One operator request; move fields are required only for promote/rollback."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["status", "promote", "rollback"]
    family: str = Field(min_length=1, max_length=256)
    channel: Channel
    capability_id: str = ""
    expected_revision: int = Field(default=0, ge=0)
    expected_version_id: str = ""
    next_version_id: str = ""
    evaluation_id: str = ""
    change_ref: str = ""


def _mutation(session: Any, request: PolicyReleaseRequest) -> PolicyReleaseMutation:
    operation: Literal["promote", "rollback"] = (
        "rollback" if request.action == "rollback" else "promote"
    )
    return PolicyReleaseMutation(
        tenant_id=str(session.tenant),
        family=request.family,
        channel=request.channel,
        operation=operation,
        capability_id=request.capability_id,
        expected_revision=request.expected_revision,
        expected_version_id=request.expected_version_id or None,
        next_version_id=request.next_version_id,
        evaluation_id=request.evaluation_id or None,
        granted_scopes=frozenset(str(scope) for scope in session.scopes),
        change_ref=request.change_ref,
    )


async def _handle(session: Any, request: PolicyReleaseRequest) -> str:
    client = runtime.graph_client(str(session.tenant))
    with client.use_verified_context(session.engine_verified_context()):
        service = _service(client)
        if request.action == "status":
            pointer = await service.current(
                str(session.tenant), request.family, request.channel
            )
            return "null" if pointer is None else pointer.model_dump_json()
        moved = await service.apply(_mutation(session, request))
        return moved.model_dump_json()


def register_policy_release_tools(mcp: Any) -> None:
    """Register the release-pointer operator tool and its REST twin."""

    @mcp.tool(
        name=TOOL_NAME,
        description=(
            "Read, promote or roll back a model-policy release pointer. "
            "Actions: status, promote, rollback. Promote needs an accepted "
            "held-out PolicyEvaluation; every move is a compare-and-swap on "
            "the expected revision and version."
        ),
        tags={"graph-os", "policy-evolution", "release"},
    )
    async def graph_policy_release(request: PolicyReleaseRequest) -> str:
        with runtime.verified_tool_session_scope() as session:
            return await _handle(session, request)

    runtime.REGISTERED_TOOLS[TOOL_NAME] = graph_policy_release
