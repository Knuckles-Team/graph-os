# GRAPHOS-ACCEPTANCE-001 — Implementation tasks

Status: SPECIFIED. Governing spec: [spec.md](spec.md). Design: [plan.md](plan.md).

- [x] Inventory `scripts/check_public_specs.py`'s existing evidence schema and structural validation to avoid duplicating its authority.
- [x] `GRAPHOS-ACCEPTANCE-R001.1`: implement the typed `EvidenceRecord`/`RequirementGap` model and `gaps_for_status`/`load_status` functions plus the standalone CLI entry point in `scripts/check_acceptance_evidence.py`.
- [x] `GRAPHOS-ACCEPTANCE-R001.1`: add the refusal test for a missing/malformed `status.json` alongside the gap-detection unit tests in `tests/test_check_acceptance_evidence.py`.
- [ ] `GRAPHOS-ACCEPTANCE-R002`: wire an auditor-identity check (reviewer distinct from the `merged_head` commit author) into the review/merge convention for an `acceptance_state` change.
- [ ] `GRAPHOS-ACCEPTANCE-R003`: add the append-only regression test comparing two revisions of a `status.json`.
- [ ] `GRAPHOS-ACCEPTANCE-R004`: extend the fixture suite with the stale-operational-evidence case once R001.1 lands.
- [ ] `GRAPHOS-ACCEPTANCE-R005`: wire the checker into the repository's pre-commit/CI spec-check group as an informational (non-blocking) worklist report.
- [ ] Run focused tests and `scripts/check_public_specs.py`; capture exact revision and results in `status.json`.
- [ ] Review evidence and change `acceptance_state` only after required gates and the distinct-auditor review in `GRAPHOS-ACCEPTANCE-R002`.

## Decomposition children (tracked)

- [x] **GRAPHOS-ACCEPTANCE-R001:** Typed acceptance-evidence model and freeze-gap checker
