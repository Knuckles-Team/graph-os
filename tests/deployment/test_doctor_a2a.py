"""Doctor coverage for graph-os-owned A2A authority."""

from __future__ import annotations

import sys

from graph_os.deployment import doctor


def test_a2a_doctor_uses_graph_os_authority_without_au_protocol_module(
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "agent_utilities.protocols.a2a_epistemic", None)
    result = doctor._check_a2a_persistence()
    assert result["status"] == "ok"
    assert result["data"] == {
        "native_backend_selected": True,
        "broker_contract_complete": True,
        "bounded_configuration": True,
        "adapter_count": 2,
        "redacted": True,
    }
