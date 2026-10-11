# GRAPHOS-GWC-001 — Test specification

Status: PROPOSED. Governing spec: [spec.md](spec.md).

| Test ID | Requirement | Level | Setup and input | Expected observation | Evidence |
|---|---|---|---|---|---|
| T-001 | FR-001 | Unit | Build the dashboard router | All 15 old method and path pairs present | PENDING |
| T-002 | FR-001 | Contract | Read-only caller on mutating routes | 403; API-key caller passes; health open | PENDING |
| T-003 | FR-002 | Unit | Create with empty template or oversize data | Typed validation or 400 refusal | PENDING |
| T-004 | FR-002 | Contract | Create then get | Id and rendered output returned; same artifact read back | PENDING |
| T-005 | FR-002 | Contract | Get unknown id | 404 | PENDING |
| T-006 | FR-002 | Contract | Refresh with inline data, with resolver, and with a failing resolver | New render; resolver render; prior render preserved with ok false | PENDING |
| T-007 | FR-002 | Contract | Mount on the host app; read-only caller POSTs | Routes present; 403 on POST | PENDING |
| T-008 | FR-003 | Unit | Discover registry widgets | genius_agent absent | PENDING |

## Negative and boundary cases

Unauthenticated and under-scoped callers, malformed ids, oversize bodies, store unavailable (typed unavailable refusal, no fabricated success), and refresh of a missing artifact.

## Quality and release proof

Run the focused tests under tests/gateway, then the repository fast gates; record revision and results in tasks.md before advancing state.
