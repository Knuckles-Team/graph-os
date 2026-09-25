"""Privacy-safe operation audit payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from graph_os.api.invoke.plan import params_digest
from graph_os.api.invoke.steps import VerifiedCaller


def audit_event(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    surface: Any,
    status: str,
    audit_ref: str = "",
) -> dict[str, str]:
    """Record identity and a digest, never argument values."""

    event = {
        "op": op.id,
        "surface": surface.value,
        "principal": caller.principal,
        "tenant": caller.tenant,
        "plan_digest": params_digest(params),
        "result_status": status,
        "request_id": caller.request_id,
    }
    if audit_ref:
        event["audit_ref"] = audit_ref
    if op.id == "fleet.call":
        server, tool = params.get("server"), params.get("tool")
        if isinstance(server, str) and isinstance(tool, str):
            event["target"] = f"{server}/{tool}"
    if getattr(op.executor, "value", None) == "service":
        event["owner"] = caller.principal
    return event
