"""Typed, immutable declarations for the GraphOS operation registry."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Verb(StrEnum):
    FIND = "find"
    ASK = "ask"
    WHY = "why"
    WRITE = "write"
    ACT = "act"
    MANAGE = "manage"


class Executor(StrEnum):
    CALLER = "caller"
    SERVICE = "service"


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    ADMIN = "admin"


class PrincipalRule(StrEnum):
    ANY = "any"
    HUMAN = "human"
    HUMAN_UNDELEGATED = "human_undelegated"
    SERVICE_ONLY = "service_only"


class Confirm(StrEnum):
    NONE = "none"
    PLAN = "plan"
    CONSOLE = "console"


class Surface(StrEnum):
    MCP = "mcp"
    HTTP = "http"
    A2A = "a2a"
    CONSOLE = "console"


class Idempotency(StrEnum):
    KEY_REQUIRED = "key_required"
    NATURAL = "natural"
    NONE = "none"


class Stability(StrEnum):
    STABLE = "stable"
    BETA = "beta"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"


class AuditClass(StrEnum):
    NONE = "none"
    EVENT = "event"
    IDENTITY_CHAIN = "identity_chain"


class SubjectSource(StrEnum):
    PARAM = "param"
    CALLER_TENANT = "caller_tenant"


class EgSchemaRef(BaseModel):
    """A schema shipped in the epistemic-graph contract wheel."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str = Field(min_length=1)


class EgMethod(BaseModel):
    """A caller-context EG method binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["eg"] = "eg"
    service: str = Field(min_length=1)
    op: str = Field(min_length=1)


class Composite(BaseModel):
    """A dotted GraphOS service handler binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["composite"] = "composite"
    handler: str = Field(pattern=r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


class SubjectRef(BaseModel):
    """A request path or verified caller tenant checked before service execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    source: SubjectSource = SubjectSource.PARAM
    path: str | None = None

    @model_validator(mode="after")
    def _source_shape(self) -> SubjectRef:
        if self.source is SubjectSource.CALLER_TENANT:
            if self.path is not None:
                raise ValueError("caller tenant subject cannot carry a request path")
        elif not self.path or not self.path.strip() or self.path.startswith("$"):
            raise ValueError("parameter subject requires a non-reserved request path")
        return self


class HttpShape(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(pattern=r"^/")


class OpSpec(BaseModel):
    """An operation's stable wire identity and authority requirements."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    id: str
    verb: Verb
    summary: str = Field(min_length=1)
    examples: tuple[str, ...] = Field(min_length=1)
    params: type[BaseModel] | EgSchemaRef
    result: type[BaseModel] | EgSchemaRef
    binding: Annotated[EgMethod | Composite, Field(discriminator="kind")]
    executor: Executor = Executor.CALLER
    scopes: frozenset[str] = frozenset()
    executor_scopes: frozenset[str] = frozenset()
    subject: SubjectRef | None = None
    effect: Effect = Effect.READ
    principals: PrincipalRule = PrincipalRule.ANY
    confirm: Confirm | None = None
    surfaces: frozenset[Surface] = frozenset({Surface.MCP, Surface.HTTP, Surface.A2A})
    http: HttpShape | None = None
    idempotency: Idempotency = Idempotency.NONE
    stability: Stability = Stability.STABLE
    remove_in: str | None = None
    audit: AuditClass = AuditClass.NONE

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", value
        ):
            raise ValueError("operation id must be a stable dotted name")
        return value

    @field_validator("examples")
    @classmethod
    def _nonempty_examples(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not example.strip() for example in value):
            raise ValueError("examples must contain nonempty text")
        return value

    def _check_executor_authority(self) -> None:
        if self.executor is Executor.CALLER and (self.executor_scopes or self.subject):
            raise ValueError("caller executor cannot claim service authority")
        if self.executor is Executor.SERVICE and (
            not self.executor_scopes or not self.subject
        ):
            raise ValueError("service executor requires executor_scopes and subject")

    def _check_stability_lifecycle(self) -> None:
        if self.stability is Stability.DEPRECATED and not self.remove_in:
            raise ValueError("deprecated operation requires remove_in")
        if self.stability is not Stability.DEPRECATED and self.remove_in:
            raise ValueError("remove_in is reserved for deprecated operations")

    def _check_surfaces_and_audit(self) -> None:
        if not self.surfaces:
            raise ValueError("operation requires at least one surface")
        if self.effect is not Effect.READ and self.audit is AuditClass.NONE:
            raise ValueError("mutating operation requires an audit class")

    def _apply_default_confirm(self) -> None:
        if self.confirm is None:
            default = {Effect.DESTRUCTIVE: Confirm.PLAN, Effect.ADMIN: Confirm.CONSOLE}
            object.__setattr__(self, "confirm", default.get(self.effect, Confirm.NONE))

    @model_validator(mode="after")
    def _authority_shape(self) -> OpSpec:
        self._check_executor_authority()
        self._check_stability_lifecycle()
        self._check_surfaces_and_audit()
        self._apply_default_confirm()
        return self
