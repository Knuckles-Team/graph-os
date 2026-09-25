"""Focused checks for the GraphOS-owned bundled skill validator."""

from __future__ import annotations

from pathlib import Path

import yaml

from graph_os.deployment.skills import validation


def test_packaged_bundle_and_forward_matrix_are_valid() -> None:
    assert validation.validate() == []


def test_forward_matrix_rejects_unknown_domain_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    matrix = yaml.safe_load(validation.FORWARD_MATRIX.read_text(encoding="utf-8"))
    matrix["cases"][0]["expected_routes"].append("unowned_domain_verb")
    candidate = tmp_path / "runtime_validation.yaml"
    candidate.write_text(yaml.safe_dump(matrix, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(validation, "FORWARD_MATRIX", candidate)

    errors = validation._validate_forward_matrix(validation._sidecar_parser())

    assert any("routes not owned by the domain skill" in error for error in errors)


def test_owner_manifest_seam_fails_closed(monkeypatch) -> None:
    def unavailable():
        raise ImportError("owner contract unavailable")

    monkeypatch.setattr(validation, "_owner_manifest_port", unavailable)

    errors = validation._validate_architecture_owner_manifest({"cases": []})

    assert errors == [
        "architecture owner manifest: unavailable (ImportError: owner contract unavailable)"
    ]


def test_candidate_must_match_au_owner_projection(monkeypatch) -> None:
    class Owner:
        def load_architecture_owner_manifest(self):
            return {"components": []}

        def architecture_candidate_from_owner_manifest(
            self, manifest, *, component_id=None
        ):
            return {"component_id": component_id, "source_digest": "expected"}

    monkeypatch.setattr(validation, "_owner_manifest_port", lambda: Owner())
    data = {
        "cases": [
            {
                "id": "development-direct",
                "skill": "agent-utilities-development",
                "architecture_candidate": {
                    "component_id": "example",
                    "source_digest": "forged",
                },
            }
        ]
    }

    assert validation._validate_architecture_owner_manifest(data) == [
        "development-direct: architecture_candidate is not bound to owner manifest"
    ]
