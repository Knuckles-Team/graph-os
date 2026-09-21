"""Bounded messages emitted to the browser-local dispatcher."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from graph_os.browser_control.browser_control_common import (
    _DIGEST,
    _ERROR_CODE,
    PROTOCOL_VERSION,
    _bounded_json,
    _identifier,
)

_Identifier = Annotated[str, AfterValidator(_identifier)]
_Digest = Annotated[str, Field(pattern=_DIGEST.pattern)]
_MachineCode = Annotated[str, Field(pattern=_ERROR_CODE.pattern)]
_JsonObject = Annotated[dict[str, Any], AfterValidator(_bounded_json)]


class _WireMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["webmcp.control.v1"] = PROTOCOL_VERSION
    type: str


class ConfirmationRequestMessage(_WireMessage):
    type: Literal["control.confirmation_request"] = "control.confirmation_request"
    call_id: _Identifier
    lease_id: _Identifier
    tool_id: _Identifier
    arguments: _JsonObject
    confirmation_digest: _Digest

    @model_validator(mode="after")
    def _bounded_message(self) -> ConfirmationRequestMessage:
        _bounded_json(self.model_dump(mode="json"))
        return self


class ControlCallMessage(_WireMessage):
    type: Literal["control.call"] = "control.call"
    call_id: _Identifier
    lease_id: _Identifier
    tool_id: _Identifier
    arguments: _JsonObject
    authorization: Literal["read", "confirmed_mutation"]
    confirmation_digest: _Digest | None = None

    @model_validator(mode="after")
    def _confirmation_matches_authorization(self) -> ControlCallMessage:
        _bounded_json(self.model_dump(mode="json"))
        expects_digest = self.authorization == "confirmed_mutation"
        if expects_digest != (self.confirmation_digest is not None):
            raise ValueError("confirmation digest does not match call authorization")
        return self


class ControlCancelMessage(_WireMessage):
    type: Literal["control.cancel"] = "control.cancel"
    call_id: _Identifier
    reason: _MachineCode = Field(min_length=1, max_length=64)


BrowserServerMessage = (
    ConfirmationRequestMessage | ControlCallMessage | ControlCancelMessage
)
BrowserSender = Callable[[BrowserServerMessage], Awaitable[None]]

__all__ = [
    "BrowserSender",
    "BrowserServerMessage",
    "ConfirmationRequestMessage",
    "ControlCallMessage",
    "ControlCancelMessage",
]
