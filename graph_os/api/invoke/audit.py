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
) -> dict[str, str]:
    """Record identity and a digest, never argument values."""

    return {
        "op": op.id,
        "surface": surface.value,
        "principal": caller.principal,
        "tenant": caller.tenant,
        "plan_digest": params_digest(params),
        "result_status": status,
        "request_id": caller.request_id,
    }
