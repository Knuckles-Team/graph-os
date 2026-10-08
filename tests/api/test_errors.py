"""The same refusal contract is used by REST, MCP, and A2A projections."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import pytest

from graph_os.api.errors import (
    FleetRefusal,
    GraphOSErrorCode,
    GraphOSRefusal,
    a2a_error_status,
    to_envelope,
)

CONTEXT = {
    "op": "identity.users.disable",
    "request_id": "request-1",
    "registry_digest": "a" * 64,
}


@dataclass
class InvokeError:
    """A fake structurally satisfying ``graph_os.api.errors.OpErrorLike``."""

    code: str
    details: Mapping[str, Any] = field(default_factory=dict)
    source: str = "graphos"


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


@pytest.mark.parametrize("sequence", [0, 1, (1 << 64) - 1])
@pytest.mark.parametrize("refusal_type", [InvokeError, GraphOSRefusal])
def test_indeterminate_projects_audit_adapter_pair(sequence, refusal_type) -> None:
    # B's EgAuditAdapter.preflight serializes [request digest, reservation seq].
    # This is its actual pair shape, not the test-only receipt:<digest> format.
    digest = "abcdef0123456789" * 4
    details = {
        "audit_ref": json.dumps([digest, sequence], separators=(",", ":")),
        "reason": "audit outcome unavailable",
        "backend": "tenant-secret@example.invalid",
    }
    status, envelope = to_envelope(
        refusal_type(GraphOSErrorCode.INDETERMINATE, details=details), **CONTEXT
    )
    reference = f"graphos_audit:{digest}:{sequence}"
    assert status == 500
    assert envelope["error"]["retryable"] is False
    assert envelope["error"]["details"] == {
        "audit_ref": reference,
        "reason": "audit outcome unavailable",
    }
    assert len(reference) <= 99
    assert "tenant-secret" not in json.dumps(envelope)


@pytest.mark.parametrize(
    "reference",
    [
        None,
        True,
        1,
        [],
        {},
        b"reference",
        ["a" * 64, 1],
        {"request_id": "a" * 64, "reservation_seq": 1},
        "",
        "not json",
        "receipt:" + "a" * 64,
        "graphos_audit:" + "a" * 64 + ":1",
        json.dumps(["a" * 64]),
        json.dumps(["a" * 64, 1, "private"]),
        json.dumps(["a" * 63, 1]),
        json.dumps(["a" * 65, 1]),
        json.dumps(["A" * 64, 1]),
        json.dumps(["g" * 64, 1]),
        json.dumps(["a" * 63 + "\n", 1]),
        json.dumps(["a" * 63 + "é", 1]),
        json.dumps([True, 1]),
        json.dumps([None, 1]),
        json.dumps(["a" * 64, True]),
        json.dumps(["a" * 64, 1.0]),
        json.dumps(["a" * 64, "1"]),
        json.dumps(["a" * 64, None]),
        json.dumps(["a" * 64, -1]),
        json.dumps(["a" * 64, 1 << 64]),
        json.dumps(["person@example.invalid", 1]),
        json.dumps(["Bearer tenant-secret", 1]),
        json.dumps(["https://private.invalid/tenant", 1]),
        json.dumps(["a" * 64, "tenant-secret"]),
        " " * 10000 + json.dumps(["a" * 64, 1]),
        "[" * 10000,
    ],
)
def test_indeterminate_rejects_invalid_or_private_audit_reference(reference) -> None:
    _, envelope = to_envelope(
        InvokeError("INDETERMINATE", {"audit_ref": reference, "raw": "private"}),
        **CONTEXT,
    )
    assert envelope["error"]["details"] == {}


def test_audit_reference_bounds_input_before_json_parse(monkeypatch) -> None:
    from graph_os.api import errors

    reference = json.dumps(["a" * 64, (1 << 64) - 1], separators=(",", ":"))
    assert len(reference) == 89
    _, valid = to_envelope(
        InvokeError("INDETERMINATE", {"audit_ref": reference}), **CONTEXT
    )
    assert len(valid["error"]["details"]["audit_ref"]) == 99

    def unexpected_parse(value):
        pytest.fail("overlength audit reference reached JSON parser")

    monkeypatch.setattr(errors.json, "loads", unexpected_parse)
    _, rejected = to_envelope(
        InvokeError("INDETERMINATE", {"audit_ref": reference + " "}), **CONTEXT
    )
    assert rejected["error"]["details"] == {}


@pytest.mark.parametrize("code", ["INTERNAL", "TIMEOUT", "UNAVAILABLE"])
def test_audit_reference_is_only_projected_for_indeterminate(code) -> None:
    _, envelope = to_envelope(
        InvokeError(code, {"audit_ref": json.dumps(["a" * 64, 1])}), **CONTEXT
    )
    assert envelope["error"]["details"] == {}


@pytest.mark.parametrize("source", ["fleet", "engine"])
def test_audit_reference_is_never_projected_for_other_sources(source, monkeypatch):
    generated = ModuleType("graph_os.api.generated.engine_errors")
    generated.ENGINE_ERRORS = {"INDETERMINATE": (500, False)}
    monkeypatch.setitem(sys.modules, generated.__name__, generated)
    _, envelope = to_envelope(
        InvokeError("INDETERMINATE", {"audit_ref": json.dumps(["a" * 64, 1])}, source),
        **CONTEXT,
    )
    assert envelope["error"]["details"] == {}


@pytest.mark.parametrize("source", ["graphos", "engine", "unknown"])
def test_audit_reference_does_not_bypass_unknown_code_refusal(source, monkeypatch):
    from graph_os.api.registry.eg_binding import EgContractError

    generated = ModuleType("graph_os.api.generated.engine_errors")
    generated.ENGINE_ERRORS = {}
    monkeypatch.setitem(sys.modules, generated.__name__, generated)
    expected = EgContractError if source == "engine" else ValueError
    with pytest.raises(expected):
        to_envelope(
            InvokeError(
                "NOT_IN_CONTRACT", {"audit_ref": json.dumps(["a" * 64, 1])}, source
            ),
            **CONTEXT,
        )


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


def test_engine_source_without_generated_contract_fails(monkeypatch) -> None:
    from graph_os.api.registry.eg_binding import EgContractError

    monkeypatch.setitem(sys.modules, "graph_os.api.generated.engine_errors", None)
    with pytest.raises(EgContractError):
        to_envelope(InvokeError("AUTH_TENANT_MISMATCH", source="engine"), **CONTEXT)
    with pytest.raises(EgContractError):
        a2a_error_status("AUTH_TENANT_MISMATCH", source="engine")


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


def test_invocation_keeps_structured_child_code_and_bounds_provenance() -> None:
    refusal = InvokeError(
        "CHILD_BUSY", {"server": "search", "tool": "query", "token": "secret"}, "fleet"
    )
    status, envelope = to_envelope(refusal, **CONTEXT)
    assert status == 502
    assert envelope["error"]["source"] == "fleet"
    assert envelope["error"]["code"] == "CHILD_BUSY"
    assert envelope["error"]["details"] == {"server": "search", "tool": "query"}
    assert a2a_error_status(refusal.code, source="fleet") == (-32000, 502)
    assert "secret" not in str(envelope)


@pytest.mark.parametrize("retryable", [False, True])
def test_generated_engine_table_controls_envelope(monkeypatch, retryable):
    from graph_os.api.errors import EngineRefusal
    from graph_os.api.registry.eg_binding import EgContractError

    generated = ModuleType("graph_os.api.generated.engine_errors")
    generated.ENGINE_ERRORS = {"SYNTHETIC_REFUSAL": (409, retryable)}
    monkeypatch.setitem(sys.modules, generated.__name__, generated)
    refusal = EngineRefusal("SYNTHETIC_REFUSAL", "tenant-secret", {"token": "secret"})
    status, envelope = to_envelope(refusal, **CONTEXT)
    assert status == 409
    assert envelope["error"]["code"] == refusal.code
    assert envelope["error"]["source"] == "engine"
    assert envelope["error"]["retryable"] is retryable
    assert envelope["error"]["details"] == {}
    assert "tenant-secret" not in str(envelope)
    assert a2a_error_status(refusal.code, source="engine") == (-32000, 409)
    with pytest.raises(EgContractError):
        to_envelope(EngineRefusal("UNKNOWN"), **CONTEXT)
