"""Typed public contracts for the GraphOS unary A2A facade."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

__all__ = [
    "A2AAgentCard",
    "A2AAgentCapabilities",
    "A2AContextBudget",
    "A2AListResult",
    "A2AMessage",
    "A2APushNotificationAuthority",
    "A2ARouteDecision",
    "A2ASkill",
    "A2AStreamingAuthority",
    "A2ATask",
    "A2ATaskState",
    "A2ATaskStatus",
    "A2ATextPart",
    "A2ATransitionHistoryAuthority",
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


@runtime_checkable
class A2AStreamingAuthority(Protocol):
    """Durable, restart-safe event-cursor-backed task-streaming authority.

    GRAPHOS-A2A-R001's open streaming slice; no implementation is wired yet.
    """

    def resume_from_cursor(self, task_id: str, cursor: str | None) -> Any: ...


@runtime_checkable
class A2APushNotificationAuthority(Protocol):
    """Durable push-notification delivery authority for task state changes."""

    def register_push_target(self, task_id: str, target_url: str) -> Any: ...


@runtime_checkable
class A2ATransitionHistoryAuthority(Protocol):
    """Durable, append-only WorkItem transition-history authority.

    Owned by epistemic-graph, not GraphOS; see GRAPHOS-A2A-003.
    """

    def transition_history(self, task_id: str) -> Any: ...


class A2AAgentCapabilities(_WireModel):
    """Capabilities advertised on the Agent Card.

    Each field defaults to ``False`` and can only become ``True`` through
    :meth:`from_wired_authorities`, which requires an actual durable
    authority object implementing the matching protocol. A bare boolean
    can never force a capability true, so the card cannot advertise more
    than GraphOS can actually serve.
    """

    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False

    @classmethod
    def from_wired_authorities(
        cls,
        *,
        streaming_authority: A2AStreamingAuthority | None = None,
        push_notification_authority: A2APushNotificationAuthority | None = None,
        transition_history_authority: A2ATransitionHistoryAuthority | None = None,
    ) -> A2AAgentCapabilities:
        """Derive capabilities truthfully from the authorities actually wired.

        Raises ``TypeError`` (a refusal, not a silent downgrade) if a
        supplied authority does not implement its required protocol.
        """
        for label, authority, protocol in (
            ("streaming_authority", streaming_authority, A2AStreamingAuthority),
            (
                "push_notification_authority",
                push_notification_authority,
                A2APushNotificationAuthority,
            ),
            (
                "transition_history_authority",
                transition_history_authority,
                A2ATransitionHistoryAuthority,
            ),
        ):
            if authority is not None and not isinstance(authority, protocol):
                raise TypeError(
                    f"{label} must implement {protocol.__name__} or be None"
                )
        return cls(
            streaming=streaming_authority is not None,
            push_notifications=push_notification_authority is not None,
            state_transition_history=transition_history_authority is not None,
        )


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
