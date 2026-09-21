"""Validated messages accepted from the browser control channel."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from graph_os.browser_control.browser_control_common import (
    _DIGEST,
    _RESULT_ERROR_CODE,
    PROTOCOL_VERSION,
    CancellationEffect,
    _bounded_json,
    _identifier,
)
from graph_os.browser_control.browser_control_descriptor import BrowserToolDescriptor

_Identifier = Annotated[str, AfterValidator(_identifier)]
_Digest = Annotated[str, Field(pattern=_DIGEST.pattern)]
_ResultError = Annotated[str, Field(pattern=_RESULT_ERROR_CODE.pattern)]


class _WireMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["webmcp.control.v1"] = PROTOCOL_VERSION
    type: str


class CatalogRegisterMessage(_WireMessage):
    type: Literal["catalog.register"] = "catalog.register"
    authority: Literal["browser-local"]
    route_id: _Identifier = Field(min_length=1, max_length=128)
    registration_generation: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    catalog_digest: _Digest
    tool_scope_digest: _Digest
    tools: tuple[BrowserToolDescriptor, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _unique_tools(self) -> CatalogRegisterMessage:
        ids = [tool.tool_id for tool in self.tools]
        if len(ids) != len(set(ids)):
            raise ValueError("catalog tool ids must be unique")
        _bounded_json(self.model_dump(mode="json"))
        return self


class ControlConfirmMessage(_WireMessage):
    type: Literal["control.confirm"] = "control.confirm"
    call_id: _Identifier
    confirmation_digest: _Digest


class ControlResultMessage(_WireMessage):
    type: Literal["control.result"] = "control.result"
    call_id: _Identifier
    status: Literal["succeeded", "failed"]
    result: Any | None = None
    error_code: _ResultError | None = None

    @model_validator(mode="after")
    def _bounded_result(self) -> ControlResultMessage:
        _bounded_json(self.model_dump(mode="json"))
        _bounded_json(self.result, limit=1_500)
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("failed results require one safe error_code")
        return self


class ControlCancelledMessage(_WireMessage):
    type: Literal["control.cancelled"] = "control.cancelled"
    call_id: _Identifier
    effect: CancellationEffect


BrowserClientMessage = (
    CatalogRegisterMessage
    | ControlConfirmMessage
    | ControlResultMessage
    | ControlCancelledMessage
)


__all__ = [
    "BrowserClientMessage",
    "CatalogRegisterMessage",
    "ControlCancelledMessage",
    "ControlConfirmMessage",
    "ControlResultMessage",
]
