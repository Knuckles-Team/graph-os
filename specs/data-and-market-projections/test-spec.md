# GRAPHOS-DATA-MARKET — test and acceptance contract

Tests use local graph-engine and PostgreSQL/MariaDB fixtures plus a deterministic
market provider stub. No test depends on a private network, operator inventory,
real brokerage account, or live notification channel. Keep source fixtures
small, versioned, and synthetic.

| ID | Level | Scenario and expected evidence |
|---|---|---|
| SC-01 | unit | Reject unknown `intent`, oversized limit/depth, malformed source ID, extra request fields; zero downstream calls. |
| SC-02 | integration | Declared FK, inferred join and proposal, approved ontology class, owning app, and view/mapping impact return with stable IDs, catalog revision, provenance, and bounded `truncated` flag. |
| SC-03 | security | Tenant B asks for A's source or guessed table: same nonrevealing result as absent table, zero engine dispatch for denied scope. |
| SC-04 | contract | Missing/incompatible graph-engine schema capability or no approved catalog returns typed `UNAVAILABLE`; no fabricated facts. |
| SC-05 | served | MCP and REST requests on the same fixture yield equivalent normalized response and correlation/audit record. |
| AD-01 | unit | Dry-run computes requirements and rollback steps without mutation or connector retirement. |
| AD-02 | integration | Local Postgres and MariaDB fixture each pass catalog, mapping, conformance, parity and activation with explicit approval. |
| AD-03 | negative | Unapproved mapping, broad grant, missing conformance or missing rollback proof blocks approval and causes no attach call. |
| AD-04 | failure | Inject failure at each attach/verify/activate transition; routing restores old path and connector stays active. Unknown detach effect remains reconcilable. |
| AD-05 | durability | Duplicate idempotency key and restart/replay produce one admission record and one attach effect; concurrent admissions for the same app serialize. |
| AD-06 | security | Tenant B cannot inspect, approve, or roll back tenant A's admission; no source calls on denial. |
| FI-01 | contract | Market response contains exact decimal strings, source, as-of, currency/unit, session, and staleness; bounded range/rows/time. |
| FI-02 | security | `finance:read` denial and missing tenant/account binding cause zero provider calls for market/position reads. Two tenants never share account responses. |
| FI-03 | parity | REST and MCP finance routes use the same service and normalized result/error; no second provider client. |
| FI-04 | schedule | Backfill and scan replay with same run key yield one work item; DST spring/fall cases follow specified policy. |
| FI-05 | explanation | All numerical claims cite input IDs; absent facts are labelled unknown; output cannot authorize order. |
| FI-06 | event | Duplicate/out-of-order finance events and process restart yield one user-visible delivery per subscriber/channel, durable cursor and retry receipt. |
| FI-07 | safety | Alerts and DCA-due messages cannot invoke paper or live order endpoints; hostile event payload is rejected. |
| FI-08 | paper | Without `finance:paper-trade`, tenant-bound account and lease, paper submit returns typed failure before any provider effect; repeated valid key yields one receipt. |
| FI-09 | isolation | Concurrent A/B market reads, schedules, and alerts preserve tenant scoping and data minimization. |
| FI-10 | DCA recurrence and execution | Duplicate due events, DST ambiguity and worker restart create one proposal per plan revision/occurrence. Paused or revoked plans create no effect. Paper mode uses a paper grant/lease; live mode without a human-approved exact preview and connector lease creates no provider call. Changed quote, amount, account, mode or policy invalidates the prior approval. An uncertain connector outcome enters reconciliation and is not blindly resent. |

## Fresh checkout execution

1. Clone `graph-os` and follow this repository's `AGENTS.md` for `uv sync
   --extra test`. The release workflow supplies pinned public sibling
   dependencies; a local contributor may use published packages or clone their
   public sources using the repository's documented setup.
2. Start fixture graph engine and disposable PostgreSQL/MariaDB services with
   public test configuration. Seed synthetic `sample_app.customer` and
   `sample_app.order` tables and a fake provider account per tenant. Generate
   no fixture with real account or source data.
3. Run focused unit/contract tests, then `uv run pytest`, `uv run ruff check .`,
   `uv run ruff format --check .`, `uv run mypy graph_os`, and the applicable
   repository hooks. Run CCCC, jscpd, dupehound and KISS review on changed code.
4. Start one GraphOS server and call both MCP and REST surfaces with A/B
   identities. Capture sanitized request IDs, response schemas, denied-call
   counters, restart/replay results, and the exact tested revision.

## Acceptance record

| Check | Revision / command / artifact | Result |
|---|---|---|
| SC-01–05 | pending | NOT RUN |
| AD-01–06 | pending | NOT RUN |
| FI-01–10 | pending | NOT RUN |
| full repository quality gates | pending | NOT RUN |
| served local fixture | pending | NOT RUN |

Mark delivery `LANDED` only from a merged default-branch revision. Mark
acceptance `ACCEPTED` only after all applicable rows pass on that revision.
