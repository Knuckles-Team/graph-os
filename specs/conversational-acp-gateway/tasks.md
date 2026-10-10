# GRAPHOS-ACP-001 — Implementation tasks

Status: BUILDING. Governing [spec](spec.md) and [plan](plan.md).

- [ ] Inventory existing `graph_os.identity` principal/session types and status/doctor reporting; record exact reusable interfaces.
- [x] `R001.1`: implement the typed ACP session-policy model and admission check (unauthenticated, over-limit and `chat_adapter_unavailable` refusals) with tests. No transport wiring in this slice.
- [ ] `R001.2`: wire the admission check behind the actual ACP endpoint once agent-utilities' chat adapter is reachable.
- [ ] `R001.3`: wire idle/expiry policy into status/doctor reporting.
- [ ] Capture exact revision and focused-test evidence; update `status.json` only after the corresponding proof exists.

## Decomposition children (tracked)

- [x] **GRAPHOS-ACP-R001:** Conversational ACP gateway session admission and policy
- [ ] **GRAPHOS-ACP-R001.1:** Remaining scope of GRAPHOS-ACP-R001 (slice .1): Conversational ACP gateway session admission and policy
- [ ] **GRAPHOS-ACP-R001.2:** Remaining scope of GRAPHOS-ACP-R001 (slice .2): Conversational ACP gateway session admission and policy
