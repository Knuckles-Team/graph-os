# GRAPHOS-A2A — test contract

## A2A task and approval projection
Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. Governing spec: [GRAPHOS-A2A](spec.md).

| Test | Requirement | Setup and expected result |
|---|---|---|
| A2A-T01 | A2A-01 | With fixture human and tenant, fetch filtered Agent Card, send a task with idempotency key, replay key and get same task, list it by cursor, cancel pending task. Missing key and foreign-tenant task refuse. |
| A2A-T02 | A2A-01, A2A-02 | Start local ephemeral engine/agent services. Stream ordered durable events; disconnect and resubscribe with cursor after restart. Each event arrives once by ID, terminal state persists, and task ID resolves to the same parent WorkItem/child relation. |
| A2A-T03 | A2A-02 | Assembly returns only caller-allowed agent skills and budgeted fleet items. Revoke a scope, refresh card and dispatch, and verify removed skill plus no alternate route. Missing upstream assembly returns `UNAVAILABLE`. |
| A2A-T04 | A2A-03 | Invoke read, write and fleet call via A2A, MCP and HTTP with the same principal. Codes, effect gate, audit shape and registry digest agree. Unknown op/method and forged actor/tenant fields refuse without executing. |
| A2A-T05 | A2A-04, A2A-05 | Pause on a PLAN effect; inspect durable `input-required` event and safe preview, restart GraphOS, sign confirmation as original human, consume fence once, observe exactly one effect and one linked audit outcome. |
| A2A-T06 | A2A-05 | Table-drive different human, service/agent token, foreign tenant, invalid signature, missing/revoked grant, altered op/params/pending call/parent-child relation, stale policy/digest, expired lease, replay, canceled task and concurrent confirm. All return a distinct stable refusal with zero extra effects. |
| A2A-T07 | A2A-04, A2A-05 | Trigger identity admin or live approval requiring console. A2A returns `STEP_UP_REQUIRED` and safe URL; A2A confirm refuses; same-human fresh MFA console flow succeeds after two-person rule where required. |
| A2A-T08 | A2A-06 | Audit reservation outage prevents effect. Simulate outcome write failure after effect: response is indeterminate with reconciliation handle; no rollback claim. Error bodies omit token, tenant and private actor values. |
| A2A-T09 | A2A-07 | Clean public clone with pinned published dependencies passes contract suite and package build; no private service or sibling tree. CI-provisioned local served test passes streaming/approval/restart on exact commit. |

Run `uv run pytest tests/a2a tests/api`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy graph_os`, `uvx --from pre-commit==4.6.0 pre-commit run --all-files`, and `uv build --wheel --out-dir dist`. CCCC and KISS remain within configured limits, jscpd records zero new pairs, and Dupehound records no new clones. Capture exact commit, command, versions, result and public CI link in a local `evidence.md` before changing delivery to `ACCEPTED`.

## A2A streaming, push notifications and transition-history projection
Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED** except `GRAPHOS-A2A-R009.1`. Governing [spec](spec.md).

| Test | Requirement | Setup and expected result |
|---|---|---|
| A2A-HT01 | A2A-H01, GRAPHOS-A2A-R009.1 | No authorities supplied: `A2AAgentCapabilities.from_wired_authorities()` returns all-false; `A2AAgentCapabilities()` default also all-false. Landed and proven by `tests/a2a/test_models.py::test_default_capabilities_are_all_false` and `::test_from_wired_authorities_with_none_stays_false`. |
| A2A-HT02 | A2A-H01, GRAPHOS-A2A-R009.1 | A conforming authority object for one capability is supplied: only that field is true, the other two stay false. Landed and proven by `tests/a2a/test_models.py::test_from_wired_authorities_advertises_only_what_is_wired` and `::test_from_wired_authorities_all_three_wired`. |
| A2A-HT03 | A2A-H01, GRAPHOS-A2A-R009.1 | A non-conforming object (missing the protocol's method) is supplied for any of the three authority kwargs: `from_wired_authorities` raises `TypeError`; no field is silently set true. Landed and proven by `tests/a2a/test_models.py::test_from_wired_authorities_refuses_non_conforming_object`. |
| A2A-HT04 | A2A-H02 | With a scripted AU WorkItem event fixture, `message/stream` emits ordered status/step/terminal events matching the fixture; a missing or stale event in the fixture yields a typed `UNAVAILABLE`, not a fabricated event. (Open — depends on `GRAPHOS-A2A`.) |
| A2A-HT05 | A2A-H03 | With a scripted EG durable-kernel fixture, `tasks/get` with history requested returns exactly the fixture's transition ledger. (Open — depends on `GRAPHOS-A2A`.) |
| A2A-HT06 | A2A-H04 | No `A2ATransitionHistoryAuthority` wired, or the wired fixture authority raises on call: `tasks/get` history returns `A2ATransitionHistoryUnavailable` with a privacy-safe message and no history data. (Open.) |
| A2A-HT07 | A2A-H05 | Push target fails once then recovers: delivered once via the durable outbox, surviving a simulated process restart. A target that fails repeatedly past the outbox's configured attempt budget is dead-lettered and reported. (Open.) |
| A2A-HT08 | A2A-H06 | With the `GRAPHOS-A2A` dependency flag unset, composition refuses to wire a real streaming/push/history authority; capability fields stay false regardless of attempted wiring. (Open.) |
| A2A-HT09 | A2A-H07 | Integration fixture exercises a chosen subset of streaming/push/history; a contract test asserts the Agent Card's advertised subset at that revision exactly matches what the fixture actually exercises. (Open.) |

Run `uv run pytest tests/a2a`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy graph_os`, and `uvx --from pre-commit==4.6.0 pre-commit run --files <changed>` for this PR's `GRAPHOS-A2A-R009.1` slice (A2A-HT01–HT03). The remaining rows (A2A-HT04–HT09) require `GRAPHOS-A2A` and live AU/EG fixtures and stay open until implemented. Capture exact commit, command, versions and result in a local `evidence.md` before changing delivery to `ACCEPTED`.

## A2A context-budget tool-subset admission
Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. Governing spec: [GRAPHOS-A2A-002](spec.md).

| Test | Requirement | Setup and expected result |
|---|---|---|
| A2A-ADM-T01 | GRAPHOS-A2A-R016, GRAPHOS-A2A-R016.1 | Construct a `ToolSubsetAdmissionRequest` with a representative agent graph reference and `A2AContextBudget`. Call `admit_tool_subset`. Expect `admitted is False`, `admitted_tools == ()`, `refusal_reason == "EG_ASSEMBLE_UNAVAILABLE"`, and a non-empty `provenance` naming the unavailable EG operation. |
| A2A-ADM-T02 | GRAPHOS-A2A-R019 | For both the refused result in A2A-ADM-T01 and any future admitted result, assert the decision's `provenance` field is non-empty and, on refusal, `refusal_reason` is one of the typed enumeration values. |
| A2A-ADM-T03 | GRAPHOS-A2A-R017 (open) | Once an EG assembly client is injectable: a scripted committed `AgentAssemble` answer covering the request's agent graph and budget yields progress past `EG_ASSEMBLE_UNAVAILABLE`; a scripted abstention or unavailable answer still refuses with that same reason. |
| A2A-ADM-T04 | GRAPHOS-A2A-R018 (open) | Once wired: given a committed EG answer but an envelope lacking the allowed-tool-subset field, refuse with `ENVELOPE_LACKS_ALLOWED_TOOL_SUBSET`. Given both, admit with `admitted_tools` equal to the EG answer's subset and `provenance` naming the EG decision record and the signed envelope. |
| A2A-ADM-T05 | GRAPHOS-A2A-R020 | A scripted partial or stale upstream answer (fewer tools than the agent graph requires, or an expired decision record) refuses; it never reduces to a smaller admitted subset. |
| A2A-ADM-T06 | GRAPHOS-A2A-R021 | `uv run pytest tests/a2a/test_admission.py` passes from a fresh public clone with no external network, private sibling checkout, or live identity provider. |

Run `uv run pytest tests/a2a/test_admission.py`, `uv run ruff check graph_os/a2a/admission.py tests/a2a/test_admission.py`, `uv run ruff format --check graph_os/a2a/admission.py tests/a2a/test_admission.py`, and `uv run mypy graph_os/a2a/admission.py`. A2A-ADM-T03 and A2A-ADM-T04 remain open until the EG and AU dependencies (`EG-DECISION-ENGINE-R036`, `AU-CONTROL-R035`) are callable; this PR lands A2A-ADM-T01, A2A-ADM-T02, and A2A-ADM-T06 only. Capture exact commit, command, and result before changing delivery to `ACCEPTED`.
