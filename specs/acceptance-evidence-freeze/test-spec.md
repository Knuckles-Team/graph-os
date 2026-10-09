# GRAPHOS-ACCEPTANCE-001 — Test specification

Status: SPECIFIED. Governing spec: [spec.md](spec.md).

| Test ID | Requirement | Level | Setup and input | Expected observation | Evidence |
|---|---|---|---|---|---|
| T-001 | FR-1, FR-2 | Unit | A fixture `status.json` with one requirement carrying `merged_head`, `test`, and `consumer` evidence all at the same commit. | `gaps_for_status` returns an empty list for that requirement. | PENDING |
| T-002 | FR-1, FR-2 | Unit | A fixture `status.json` with a `LANDED` requirement carrying only `merged_head` evidence. | `gaps_for_status` returns a `RequirementGap` naming that requirement with `missing_kinds` containing `test` and `operational`. | PENDING |
| T-003 | FR-4 | Unit | A fixture `status.json` with a requirement whose `consumer` receipt is recorded at an older commit than its current `merged_head`. | `gaps_for_status` reports `operational` as missing even though a `consumer` entry exists in the array. | PENDING |
| T-004 | FR-2 | Refusal | `load_status` called against a path that does not exist, and against a path containing invalid JSON. | Both calls raise `AcceptanceEvidenceError`; neither returns an empty or default-passing result. | PENDING |
| T-005 | FR-5 | CLI | `main()` invoked against the fixture directory from T-001/T-002 combined. | Process exits `1`, prints exactly the T-002 requirement's ID and missing kinds, and running it twice in a row produces byte-identical stdout. | PENDING |

## Negative and boundary cases

A requirement absent from `status.json`'s `requirements` array is out of this checker's scope (that absence is already a `check_public_specs.py` structural error). A requirement in `SPECIFIED` or `BUILDING` state is never reported as a gap — only `LANDED`/`CLOSED` rows are candidates for `ACCEPTED`. A `status.json` that is syntactically valid JSON but not an object (for example, a bare array) refuses via `AcceptanceEvidenceError` rather than raising an unrelated `AttributeError`.

## Quality and release proof

Run `python3 -m pytest tests/test_check_acceptance_evidence.py -q` from the repository root. Run `python3 scripts/check_public_specs.py` to confirm this spec's own four documents and `status.json` satisfy the existing structural gate unchanged. Record the exact commit and pass/fail in this spec's `status.json` evidence array before any requirement here moves past `SPECIFIED`.
