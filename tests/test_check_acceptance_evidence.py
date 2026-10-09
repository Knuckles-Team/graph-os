"""Pin check_acceptance_evidence.py's gap detection and refusal path.

Covers GRAPHOS-ACCEPTANCE-R001 (specs/acceptance-evidence-freeze): a fully
evidenced requirement reports no gap, a LANDED requirement missing test or
operational evidence is reported with the exact missing kinds, stale
operational evidence against a newer merged_head is still reported missing,
and a missing/malformed status.json refuses rather than returning a false
empty-gap pass.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "check_acceptance_evidence.py"
)
_SPEC = importlib.util.spec_from_file_location("check_acceptance_evidence", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _module
_SPEC.loader.exec_module(_module)

AcceptanceEvidenceError = _module.AcceptanceEvidenceError
RequirementGap = _module.RequirementGap
gaps_for_status = _module.gaps_for_status
load_status = _module.load_status
main = _module.main

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def _evidence(kind: str, commit: str, result: str = "passed") -> dict:
    return {
        "kind": kind,
        "commit": commit,
        "result": result,
        "url": f"https://github.com/Knuckles-Team/graph-os/commit/{commit}",
        "description": "fixture",
    }


def _status(requirements: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "spec_id": "FIXTURE-001",
        "owner_repo": "graph-os",
        "requirement_ids": [r["id"] for r in requirements],
        "delivery_state": "BUILDING",
        "acceptance_state": "NOT_AUDITED",
        "evidence": [],
        "requirements": requirements,
    }


def test_fully_evidenced_requirement_reports_no_gap() -> None:
    data = _status(
        [
            {
                "id": "FIX-R001",
                "title": "Fully evidenced",
                "delivery_state": "LANDED",
                "evidence": [
                    _evidence("merged_head", COMMIT_A),
                    _evidence("test", COMMIT_A),
                    _evidence("consumer", COMMIT_A),
                ],
            }
        ]
    )
    assert gaps_for_status(data) == []


def test_landed_requirement_missing_test_and_operational_is_reported() -> None:
    data = _status(
        [
            {
                "id": "FIX-R002",
                "title": "Merged only",
                "delivery_state": "LANDED",
                "evidence": [_evidence("merged_head", COMMIT_A)],
            }
        ]
    )
    gaps = gaps_for_status(data)
    assert gaps == [RequirementGap("FIX-R002", COMMIT_A, ("test", "operational"))]


def test_stale_operational_evidence_against_newer_merged_head_is_missing() -> None:
    data = _status(
        [
            {
                "id": "FIX-R003",
                "title": "Operational evidence went stale",
                "delivery_state": "LANDED",
                "evidence": [
                    _evidence("merged_head", COMMIT_A),
                    _evidence("test", COMMIT_A),
                    _evidence("consumer", COMMIT_A),
                    # A later merged_head supersedes COMMIT_A without a
                    # fresh operational receipt at COMMIT_B.
                    _evidence("merged_head", COMMIT_B),
                    _evidence("test", COMMIT_B),
                ],
            }
        ]
    )
    gaps = gaps_for_status(data)
    assert gaps == [RequirementGap("FIX-R003", COMMIT_B, ("operational",))]


def test_specified_requirement_is_never_reported() -> None:
    data = _status(
        [
            {
                "id": "FIX-R004",
                "title": "Not built yet",
                "delivery_state": "SPECIFIED",
                "evidence": [],
            }
        ]
    )
    assert gaps_for_status(data) == []


def test_load_status_refuses_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "status.json"
    with pytest.raises(AcceptanceEvidenceError):
        load_status(missing)


def test_load_status_refuses_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "status.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(AcceptanceEvidenceError):
        load_status(bad)


def test_load_status_refuses_non_object_document(tmp_path: Path) -> None:
    bad = tmp_path / "status.json"
    bad.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(AcceptanceEvidenceError):
        load_status(bad)


def test_main_reports_gap_and_exits_nonzero(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            _status(
                [
                    {
                        "id": "FIX-R005",
                        "title": "Gapped",
                        "delivery_state": "LANDED",
                        "evidence": [_evidence("merged_head", COMMIT_A)],
                    }
                ]
            )
        ),
        encoding="utf-8",
    )
    exit_code = main([str(status_path)])
    assert exit_code == 1


def test_main_is_deterministic_across_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            _status(
                [
                    {
                        "id": "FIX-R006",
                        "title": "Gapped twice",
                        "delivery_state": "LANDED",
                        "evidence": [_evidence("merged_head", COMMIT_A)],
                    }
                ]
            )
        ),
        encoding="utf-8",
    )
    main([str(status_path)])
    first = capsys.readouterr().out
    main([str(status_path)])
    second = capsys.readouterr().out
    assert first == second
