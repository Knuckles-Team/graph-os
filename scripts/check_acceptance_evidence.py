"""List LANDED/CLOSED requirements that are not yet ready for ACCEPTED.

Implements GRAPHOS-ACCEPTANCE-R001 (specs/acceptance-evidence-freeze). This
is deliberately a separate, informational tool from ``check_public_specs.py``:
that script refuses a ``status.json`` that already *claims* ``ACCEPTED``
without the required evidence; this one answers a different question for a
human auditor -- which currently-``LANDED`` rows are not yet evidenced enough
to accept, and which specific evidence kind is missing for each. It never
mutates a requirement's state and takes no network or live-cluster input.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ACCEPTABLE_STATES = frozenset({"LANDED", "CLOSED"})
OPERATIONAL_KINDS = frozenset({"consumer", "release"})


class AcceptanceEvidenceError(ValueError):
    """Raised when a status document cannot be read as a real status.json.

    Deliberately distinct from returning an empty gap list: a missing or
    malformed input must never be mistaken for "nothing to report".
    """


@dataclass(frozen=True)
class EvidenceRecord:
    kind: str
    commit: str
    result: str
    url: str


@dataclass(frozen=True)
class RequirementGap:
    requirement_id: str
    merged_head_commit: str | None
    missing_kinds: tuple[str, ...]


def load_status(path: Path) -> dict:
    """Load and parse a status.json, refusing rather than guessing.

    Raises AcceptanceEvidenceError for a missing file, invalid JSON, or a
    document that does not parse to a JSON object.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AcceptanceEvidenceError(
            f"{path}: cannot read status file ({exc})"
        ) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AcceptanceEvidenceError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise AcceptanceEvidenceError(f"{path}: status.json must be a JSON object")
    return data


def _records(evidence: object) -> list[EvidenceRecord]:
    if not isinstance(evidence, list):
        return []
    records = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        commit = item.get("commit")
        result = item.get("result")
        url = item.get("url")
        if isinstance(kind, str) and isinstance(commit, str):
            records.append(
                EvidenceRecord(
                    kind=kind,
                    commit=commit,
                    result=result if isinstance(result, str) else "",
                    url=url if isinstance(url, str) else "",
                )
            )
    return records


def _merged_head_commit(records: list[EvidenceRecord]) -> str | None:
    passed = [r for r in records if r.kind == "merged_head" and r.result == "passed"]
    if not passed:
        return None
    # The current merged head is the one most recently appended; status.json
    # evidence arrays are append-only (GRAPHOS-ACCEPTANCE-R003), so the last
    # passing merged_head entry is authoritative.
    return passed[-1].commit


def _missing_kinds_for_commit(
    records: list[EvidenceRecord], commit: str
) -> tuple[str, ...]:
    at_commit = [r for r in records if r.commit == commit and r.result == "passed"]
    missing = []
    if not any(r.kind == "test" for r in at_commit):
        missing.append("test")
    if not any(r.kind in OPERATIONAL_KINDS for r in at_commit):
        missing.append("operational")
    return tuple(missing)


def gaps_for_requirement(requirement: dict) -> RequirementGap | None:
    """Return the gap for one requirement entry, or None if it is fully ready."""
    if requirement.get("delivery_state") not in ACCEPTABLE_STATES:
        return None
    records = _records(requirement.get("evidence"))
    commit = _merged_head_commit(records)
    requirement_id = requirement.get("id", "")
    if commit is None:
        return RequirementGap(requirement_id, None, ("merged_head",))
    missing = _missing_kinds_for_commit(records, commit)
    if not missing:
        return None
    return RequirementGap(requirement_id, commit, missing)


def gaps_for_status(data: dict) -> list[RequirementGap]:
    """Return one RequirementGap per LANDED/CLOSED requirement missing evidence."""
    requirements = data.get("requirements")
    if not isinstance(requirements, list):
        return []
    gaps = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        gap = gaps_for_requirement(requirement)
        if gap is not None:
            gaps.append(gap)
    return gaps


def _discover_status_files(root: Path) -> list[Path]:
    return sorted((root / "specs").glob("*/status.json"))


def _format_gap(path: Path, gap: RequirementGap) -> str:
    head = gap.merged_head_commit or "<none>"
    missing = ",".join(gap.missing_kinds)
    return f"{path}: {gap.requirement_id} missing={missing} merged_head={head}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parents[1]
    paths = [Path(arg) for arg in argv] if argv else _discover_status_files(root)
    lines: list[str] = []
    for path in paths:
        data = load_status(path)
        for gap in gaps_for_status(data):
            lines.append(_format_gap(path, gap))
    if lines:
        print("\n".join(lines))
        print(f"{len(lines)} requirement(s) not ready for ACCEPTED.")
        return 1
    print("No LANDED/CLOSED requirement is missing ACCEPTED evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
