# GRAPHOS-ACCEPTANCE — Design and implementation plan

Status: SPECIFIED. Governing spec: [spec.md](spec.md).

## Existing system and reuse

`scripts/check_public_specs.py` already defines `EVIDENCE_KINDS`, `DELIVERY_STATES`, and `ACCEPTANCE_STATES`, and its `_receipt_errors`/`_requirement_entry_errors` functions already refuse a `status.json` that claims `ACCEPTED` or `LANDED` without a matching `merged_head` receipt. This spec does not duplicate that schema. It adds a narrower, standalone **readiness** checker that answers a different question than the existing validator: not "is this `status.json` well-formed," but "which `LANDED` rows, across one or more specs, are not yet evidenced enough for an auditor to accept them." The existing validator is a structural gate; this checker is an audit worklist generator, reusable by a human auditor before they touch `acceptance_state` at all.

## Architecture

A new module, `scripts/check_acceptance_evidence.py`, exposes:

- a typed `EvidenceRecord` and `RequirementGap` data model (the "typed acceptance-evidence model" from FR-2/R001), built with `dataclasses` to stay dependency-free like `check_public_specs.py`;
- a pure function `gaps_for_status(data: dict) -> list[RequirementGap]` that, given parsed `status.json` content, returns one `RequirementGap` per `LANDED`/`CLOSED` requirement missing `merged_head`, `test`, or operational (`consumer`/`release`) evidence at the same commit;
- a `load_status(path: Path) -> dict` loader that raises `AcceptanceEvidenceError` (never returns a false-empty result) on missing file, unreadable JSON, or a non-object document — the refusal path required by FR-2;
- a `main()` CLI entry point usable standalone (`python3 scripts/check_acceptance_evidence.py specs/<name>/status.json`) or across every tracked spec directory with no arguments, printing a deterministic gap report and a nonzero exit code when any gap exists.

No existing module is modified; `check_public_specs.py`'s structural validation keeps running unchanged in the same pre-commit/CI gate.

## Interfaces and data model

```python
@dataclass(frozen=True)
class EvidenceRecord:
    kind: str
    commit: str
    result: str
    url: str

@dataclass(frozen=True)
class RequirementGap:
    requirement_id: str
    merged_head_commit: str
    missing_kinds: tuple[str, ...]
```

`missing_kinds` is a subset of `{"test", "operational"}` plus the degenerate case where `merged_head` itself is absent (reported as `{"merged_head"}`, since no gate can proceed without it). This is additive: no existing JSON field, required key, or exit-code contract of `check_public_specs.py` changes.

## Live integration path

Entry point: a developer or CI job runs `python3 scripts/check_acceptance_evidence.py` (no args) from the repository root; it discovers every `specs/*/status.json` tracked under `specs/`, loads each with `load_status`, and prints one line per gapped requirement plus a summary count, exiting `1` if any gap exists and `0` otherwise — mirroring `check_public_specs.py`'s own `main()` contract so it can be wired into the same pre-commit hook group later without a second convention.

## Quality and release gates

Targeted pytest: `tests/test_check_acceptance_evidence.py`, covering a fully evidenced row (no gap), a `LANDED` row missing `test` evidence, a `LANDED` row whose operational evidence is stale against a newer `merged_head`, and the refusal path for a missing/malformed `status.json`. No scanner gaming: the checker only reports, it never mutates `status.json` or a requirement's state.

## Risks, alternatives, and decisions

Alternative considered: extending `check_public_specs.py`'s `_requirement_entry_errors` to also fail on an incomplete `ACCEPTED`-readiness set for `LANDED` rows. Rejected for this slice because that function enforces structural correctness of claims already made (a row that claims `ACCEPTED` without evidence is a lie and must hard-fail CI); a `LANDED` row that is simply not yet audited is not a lie and must not fail the public-specs gate. The two concerns stay in separate tools so the readiness worklist can be run informationally without blocking delivery-lane PRs.
