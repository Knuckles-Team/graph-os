"""Engine-owned RDF capability is independent of local Python extras."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from graph_os.deployment import doctor


@pytest.mark.parametrize("rdf_installed", [False, True])
def test_python_diagnostic_does_not_probe_local_rdf(
    monkeypatch: pytest.MonkeyPatch, rdf_installed: bool
) -> None:
    installed = {"psycopg"}
    if rdf_installed:
        installed.add("rdflib")
    probe = Mock(side_effect=lambda name: object() if name in installed else None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", probe)

    report = doctor.run_doctor(["python_env"])

    check = report["checks"][0]
    assert check["name"] == "python_env"
    assert check["data"] == {"postgres": True, "stardog": False}
    assert "owl/sparql" not in check["detail"]
    assert "rdflib" not in [call.args[0] for call in probe.call_args_list]
