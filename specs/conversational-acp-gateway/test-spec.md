# GRAPHOS-ACP-001 — Test specification

Status: BUILDING. Governing [spec](spec.md). All evidence is PENDING; publishing this test contract is not a test pass.

| Test ID | Requirement | Level | Fixture and input | Expected observation |
|---|---|---|---|---|
| T-ACP-01 | ACP-01 | Unit | No principal, adapter available | Typed unauthenticated refusal; no session object returned. |
| T-ACP-02 | ACP-01 | Unit | Verified principal, adapter available | Admission succeeds with a bound snapshot. |
| T-ACP-03 | ACP-02 | Unit | Admitted session, attempted field mutation on the returned snapshot | Snapshot is immutable; a second admission call for a different principal produces an independent session. |
| T-ACP-04 | ACP-03 | Unit | Admitted session, clock advanced past idle limit with no activity | Next-use check returns typed idle-expired refusal. |
| T-ACP-05 | ACP-04 | Unit | Admitted session, recent activity but clock advanced past absolute limit | Next-use check returns typed expired refusal despite recent activity. |
| T-ACP-06 | ACP-05 | Unit | Verified principal, adapter unavailable, admission attempted | Typed `chat_adapter_unavailable` refusal; no session admitted. |
| T-ACP-07 | ACP-05 | Unit | Admitted session from a prior available state, adapter goes unavailable, message attempted | Typed `chat_adapter_unavailable` refusal on the message check; no synthesized reply. |
| T-ACP-08 | ACP-06 | Integration | Same credential resolved through the existing identity path and through ACP admission | Identical principal/tenant/scope snapshot on both paths. |
| T-ACP-09 | ACP-07 | Unit | Status/doctor read with adapter unavailable and configured limits | Output reports adapter state and limits; no token or session secret present. |

## Release proof

Record the exact commit, `uv run pytest` result for the targeted module, and the fast-subset hook results (`complexity-staged`, `kiss-staged`, `dupehound-changed`, `jscpd-differential`) on the changed files. A source test alone does not establish served or integration acceptance against a real agent-utilities adapter; that remains open until a live receipt exists.
