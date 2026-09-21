"""Versioned browser-local capability descriptor."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from graph_os.browser_control.browser_control_common import (
    _DIGEST,
    MAX_SCHEMA_BYTES,
    ConfirmationPolicy,
    MutationClass,
    _bounded_json,
    _identifier,
    schema_sha256,
)

_SOURCE_REF = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_Identifier = Annotated[str, AfterValidator(_identifier)]
_Role = Annotated[str, Field(max_length=64), AfterValidator(_identifier)]
_Digest = Annotated[str, Field(pattern=_DIGEST.pattern)]


class BrowserToolDescriptor(BaseModel):
    """One browser-local capability; schemas remain in volatile channel state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: _Identifier = Field(min_length=1, max_length=128)
    version: _Identifier = Field(min_length=1, max_length=64)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    schema_digest: _Digest
    mutation_class: MutationClass
    confirmation_policy: ConfirmationPolicy
    required_roles: tuple[_Role, ...] = Field(default=(), max_length=16)
    source_ref: str

    @field_validator("source_ref")
    @classmethod
    def _source_ref(cls, value: str) -> str:
        if _SOURCE_REF.fullmatch(value) is None:
            raise ValueError("source_ref must be bounded printable provenance")
        return value

    @field_validator("required_roles")
    @classmethod
    def _roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("required_roles must be unique")
        return value

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schemas(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, limit=MAX_SCHEMA_BYTES)

    @model_validator(mode="after")
    def _consistent_descriptor(self) -> BrowserToolDescriptor:
        if self.schema_digest != schema_sha256(self.input_schema, self.output_schema):
            raise ValueError("schema_digest does not match the advertised schemas")
        expected = (
            ConfirmationPolicy.NONE
            if self.mutation_class is MutationClass.READ
            else ConfirmationPolicy.EXACT_REQUEST
        )
        if self.confirmation_policy is not expected:
            raise ValueError("confirmation policy does not match mutation class")
        return self


__all__ = ["BrowserToolDescriptor"]
