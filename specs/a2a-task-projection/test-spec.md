# A2A task and approval projection — test contract

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. Governing spec: [GRAPHOS-A2A-001](spec.md).

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
