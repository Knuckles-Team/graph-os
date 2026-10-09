"""Typed public contracts for the GraphOS unary A2A facade."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

__all__ = [
    "A2AAgentCard",
    "A2AAgentCapabilities",
    "A2AContextBudget",
    "A2AListResult",
    "A2AMessage",
    "A2AOperationInvokeParams",
    "A2APlanConfirmParams",
    "A2ARouteDecision",
    "A2ASkill",
    "A2ATask",
    "A2ATaskState",
    "A2ATaskStatus",
    "A2ATextPart",
]


class _WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class A2ATextPart(_WireModel):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=65_536)


class A2AMessage(_WireModel):
    """The text-only input shape truthfully advertised by the Agent Card."""

    role: Literal["user"]
    parts: list[A2ATextPart] = Field(min_length=1, max_length=32)
    kind: Literal["message"] = "message"
    message_id: str = Field(min_length=1, max_length=512)
    context_id: str | None = Field(default=None, min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("message metadata has too many fields")
        return value

    def task_text(self) -> str:
        return "\n".join(part.text for part in self.parts)


class A2AContextBudget(_WireModel):
    """Evaluate-only context bound used by assembly/tool-subset selection."""

    tokens: int = Field(ge=256, le=1_000_000)


class A2ARouteDecision(_WireModel):
    """A routing result; references are opaque authority-owned identifiers."""

    agent_name: str = Field(min_length=1, max_length=256)
    selected_tools: tuple[str, ...] = Field(default=(), max_length=64)
    selection_mode: str = Field(min_length=1, max_length=128)
    agent_graph_ref: str | None = Field(default=None, max_length=512)
    run_spec_ref: str | None = Field(default=None, max_length=512)
    decision_record_ref: str | None = Field(default=None, max_length=512)

    @field_validator("selected_tools")
    @classmethod
    def _unique_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(name.strip() for name in value)
        if any(not name for name in normalized) or len(set(normalized)) != len(
            normalized
        ):
            raise ValueError("selected tool identities must be non-empty and unique")
        return normalized


class A2AOperationInvokeParams(_WireModel):
    """``graphos.op/invoke`` params (GRAPHOS-A2A-R006.1).

    Mirrors the shared hosted-operation registry's ``invoke`` call shape so
    the eventual bridge passes these fields through unchanged.
    """

    op: str = Field(min_length=1, max_length=256)
    params: dict[str, Any] = Field(default_factory=dict)
    plan_ref: str | None = Field(default=None, max_length=512)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)


class A2APlanConfirmParams(_WireModel):
    """``graphos.plan/confirm`` params (GRAPHOS-A2A-R005.1/R006.1).

    The signed human approval exchange this carries is not implemented yet;
    see ``A2AOperationBridgeUnavailable``.
    """

    task_id: str = Field(min_length=1, max_length=80)
    pending_call_id: str = Field(min_length=1, max_length=256)
    plan_ref: str = Field(min_length=1, max_length=512)
    signature: str = Field(min_length=1, max_length=4096)


A2ATaskState = Literal[
    "submitted", "working", "completed", "canceled", "failed", "rejected"
]


class A2ATaskStatus(_WireModel):
    state: A2ATaskState
    timestamp: str | None = None


class A2ATask(_WireModel):
    id: str = Field(pattern=r"^a2a-[0-9a-f]{64}$")
    context_id: str = Field(pattern=r"^a2a-context-[0-9a-f]{64}$")
    kind: Literal["task"] = "task"
    status: A2ATaskStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2AListResult(_WireModel):
    tasks: list[A2ATask]
    next_cursor: str | None = None


class A2AAgentCapabilities(_WireModel):
    streaming: Literal[False] = False
    push_notifications: Literal[False] = False
    state_transition_history: Literal[False] = False


class A2ASkill(_WireModel):
    id: str
    name: str
    description: str
    tags: list[str]
    input_modes: list[str]
    output_modes: list[str]


class A2AAgentCard(_WireModel):
    name: str
    description: str
    url: str
    version: str
    protocol_version: Literal["0.3.0"] = "0.3.0"
    preferred_transport: Literal["JSONRPC"] = "JSONRPC"
    capabilities: A2AAgentCapabilities = Field(default_factory=A2AAgentCapabilities)
    security: list[dict[str, list[str]]] = Field(
        default_factory=lambda: [{"bearerAuth": ["kg:read", "kg:write"]}]
    )
    security_schemes: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "Server-validated tenant bearer identity",
            }
        }
    )
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    skills: list[A2ASkill]
