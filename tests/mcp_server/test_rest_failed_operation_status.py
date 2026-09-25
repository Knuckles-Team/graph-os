"""EH-386: graph-os's served REST twins never restate a failed operation as success.

A tool returns ``public_error_json`` (an ``OperationResult`` with
``status: "failed"``) instead of raising. The served gateway wrapped that in
``{"status": "success"}`` with HTTP 200, so an engine ``ACCESS_DENIED`` write
looked like a successful one. AU's own twins were fixed in ebca2bf1c; this pins
the graph-os copy to the same shared mapping.
"""

from __future__ import annotations

import json

import pytest
from agent_utilities.security.error_surface import public_error_payload

from graph_os.mcp_server.runtime import _tool_result_response


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("operation_failed", 500),
        ("permission_denied", 403),
        ("invalid_request", 400),
        ("dependency_unavailable", 503),
    ],
)
def test_typed_failure_maps_to_its_public_status(code: str, status: int) -> None:
    payload = public_error_payload(RuntimeError("ACCESS_DENIED: nope"), code=code)
    response = _tool_result_response("graph_write", payload, engine_domain=False)
    body = json.loads(response.body)
    assert response.status_code == status
    assert body == {"status": "failed", "result": payload}


@pytest.mark.parametrize(
    "result", [{"status": "failed"}, {"status": "ok", "nodes": 3}, "Node added.", []]
)
def test_ordinary_results_stay_success(result: object) -> None:
    response = _tool_result_response("graph_write", result, engine_domain=False)
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "success", "result": result}


def test_engine_dispatch_client_error_stays_400() -> None:
    response = _tool_result_response(
        "engine_query", {"error": "unknown action x"}, engine_domain=True
    )
    assert response.status_code == 400
    assert json.loads(response.body)["status"] == "failed"


def test_unready_fleet_evidence_stays_unavailable() -> None:
    parsed = {"evidence": {"ready": False}}
    response = _tool_result_response("graph_sessions", parsed, engine_domain=False)
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "unavailable", "result": parsed}
