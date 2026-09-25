"""The same refusal contract is used by REST, MCP, and A2A projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from graph_os.api.errors import (
    EngineRefusal,
    FleetRefusal,
    GraphOSErrorCode,
    GraphOSRefusal,
    a2a_error_status,
    to_envelope,
)
from graph_os.api.generated.engine_errors import ENGINE_ERRORS

CONTEXT = {
    "op": "identity.users.disable",
    "request_id": "request-1",
    "registry_digest": "a" * 64,
}


@dataclass(frozen=True)
class InvokeError:
    code: str
    details: dict[str, Any] = field(default_factory=dict)


def test_every_graphos_code_has_one_envelope() -> None:
    for code in GraphOSErrorCode:
        status, envelope = to_envelope(GraphOSRefusal(code), **CONTEXT)
        assert status in range(400, 600)
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == code.value
        assert envelope["error"]["source"] == "graphos"
        assert envelope["error"]["op"] == CONTEXT["op"]
        assert envelope["error"]["request_id"] == CONTEXT["request_id"]
        assert envelope["meta"] == {
            "registry_digest": CONTEXT["registry_digest"],
            "api_version": "v1",
        }


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (GraphOSErrorCode.UNAUTHENTICATED, 401, False),
        (GraphOSErrorCode.SCOPE_REQUIRED, 403, False),
        (GraphOSErrorCode.CONFIRMATION_REQUIRED, 428, False),
        (GraphOSErrorCode.POLICY_UNAVAILABLE, 503, True),
        (GraphOSErrorCode.INDETERMINATE, 500, False),
        (GraphOSErrorCode.RATE_LIMITED, 429, True),
        (GraphOSErrorCode.INTERNAL, 500, False),
    ],
)
def test_graphos_http_status_and_retry_posture(
    code: GraphOSErrorCode, status: int, retryable: bool
) -> None:
    actual_status, envelope = to_envelope(GraphOSRefusal(code), **CONTEXT)
    assert actual_status == status
    assert envelope["error"]["retryable"] is retryable


def test_invoke_error_uses_the_same_closed_code_mapping() -> None:
    status, envelope = to_envelope(
        InvokeError("SCOPE_REQUIRED", {"missing_scopes": ["identity:admin"]}),
        **CONTEXT,
    )
    assert status == 403
    assert envelope["error"]["code"] == "SCOPE_REQUIRED"
    assert envelope["error"]["details"] == {"missing_scopes": ["identity:admin"]}
    with pytest.raises(ValueError):
        to_envelope(InvokeError("NOT_IN_CONTRACT"), **CONTEXT)


def test_indeterminate_outcome_keeps_only_fixed_audit_reason() -> None:
    status, envelope = to_envelope(
        InvokeError(
            "INDETERMINATE",
            {"reason": "audit outcome unavailable", "backend": "tenant-secret"},
        ),
        **CONTEXT,
    )
    assert status == 500
    assert envelope["error"]["code"] == "INDETERMINATE"
    assert envelope["error"]["retryable"] is False
    assert envelope["error"]["details"] == {"reason": "audit outcome unavailable"}
    assert "tenant-secret" not in str(envelope)

    _, unknown_reason = to_envelope(
        InvokeError("INDETERMINATE", {"reason": "tenant-secret"}), **CONTEXT
    )
    assert unknown_reason["error"]["details"] == {}


def test_a2a_uses_the_closed_mapping_and_confirmation_is_a_result() -> None:
    assert a2a_error_status("SCOPE_REQUIRED") == (-32003, 403)
    assert a2a_error_status("UNKNOWN_OP") == (-32601, 404)
    assert a2a_error_status("INDETERMINATE") == (-32052, 500)
    with pytest.raises(KeyError):
        a2a_error_status("CONFIRMATION_REQUIRED")


@pytest.mark.parametrize(
    ("code", "include_console"),
    [
        ("CONFIRMATION_REQUIRED", False),
        ("STEP_UP_REQUIRED", True),
    ],
)
def test_confirmation_keeps_only_bounded_resumption_data(
    code: str, include_console: bool
) -> None:
    plan_ref = "graphos_plan:" + "a" * 48
    details = {
        "plan_ref": plan_ref,
        "op": CONTEXT["op"],
        "effect": "admin",
        "console_url": f"/console/confirm/{plan_ref}",
        "token": "tenant-secret",
    }
    status, envelope = to_envelope(InvokeError(code, details), **CONTEXT)
    assert status == 428
    expected = {"plan_ref": plan_ref, "op": CONTEXT["op"], "effect": "admin"}
    if include_console:
        expected["console_url"] = details["console_url"]
    assert envelope["error"]["details"] == expected
    assert "tenant-secret" not in str(envelope)


def test_confirmation_rejects_unbounded_or_forged_resume_values() -> None:
    _, envelope = to_envelope(
        InvokeError(
            "STEP_UP_REQUIRED",
            {
                "plan_ref": "graphos_plan:" + "x" * 200,
                "op": CONTEXT["op"],
                "effect": "admin",
                "console_url": "https://attacker.invalid/confirm",
            },
        ),
        **CONTEXT,
    )
    assert envelope["error"]["details"] == {}


def test_engine_codes_are_published_and_pass_through() -> None:
    assert ENGINE_ERRORS, "EG errors.json must be generated before this lane closes"
    for code, (status_hint, retryable) in ENGINE_ERRORS.items():
        status, envelope = to_envelope(
            EngineRefusal.from_response({"code": code, "message": "tenant-secret"}),
            **CONTEXT,
        )
        assert status == status_hint
        assert envelope["error"]["code"] == code
        assert envelope["error"]["source"] == "engine"
        assert envelope["error"]["retryable"] is retryable
        assert "tenant-secret" not in str(envelope)


def test_unknown_engine_code_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown engine error code"):
        EngineRefusal.from_response({"code": "NOT_IN_CONTRACT"})
    with pytest.raises(ValueError, match="unknown engine error code"):
        to_envelope(EngineRefusal("NOT_IN_CONTRACT"), **CONTEXT)


def test_graphos_details_are_allowlisted() -> None:
    _, envelope = to_envelope(
        GraphOSRefusal(
            GraphOSErrorCode.SCOPE_REQUIRED,
            "tenant-secret",
            {"missing_scopes": ["identity:admin"], "token": "token-secret"},
        ),
        **CONTEXT,
    )
    assert envelope["error"]["details"] == {"missing_scopes": ["identity:admin"]}
    assert "secret" not in str(envelope)


def test_fleet_code_and_provenance_are_preserved() -> None:
    status, envelope = to_envelope(
        FleetRefusal("CHILD_BUSY", "search", "query", "token-secret", True),
        **CONTEXT,
    )
    assert status == 502
    assert envelope["error"]["code"] == "CHILD_BUSY"
    assert envelope["error"]["source"] == "fleet"
    assert envelope["error"]["details"] == {
        "server": "search",
        "tool": "query",
    }
    assert envelope["error"]["retryable"] is True
    assert "secret" not in str(envelope)
